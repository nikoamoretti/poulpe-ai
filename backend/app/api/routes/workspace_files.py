"""Endpoints for browsing deliverables (files) inside worker workspaces."""

from __future__ import annotations

import mimetypes
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from app.api.deps import get_db
from app.core.errors import NotFoundError, ValidationError
from app.models.project import Project
from app.models.session import Session
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

router = APIRouter(prefix="/projects/{project_id}/files", tags=["workspace-files"])

# Only serve text-ish files to prevent accidental binary blobs
_TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".sh", ".bash", ".zsh", ".fish",
    ".html", ".css", ".scss", ".less", ".svg",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp",
    ".rb", ".php", ".sql", ".graphql", ".proto",
    ".env", ".gitignore", ".dockerignore", ".editorconfig",
    ".csv", ".tsv", ".log", ".xml", ".rst",
    "", # files with no extension (Makefile, Dockerfile, etc.)
}

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB


class FileEntry(BaseModel):
    name: str
    path: str  # relative to workspace root
    is_dir: bool
    size: int | None = None


class FileContent(BaseModel):
    path: str
    content: str
    size: int
    mime_type: str


def _resolve_workspace(project_id: UUID, db: DBSession) -> Path:
    """Find the workspace directory for the project's latest worker session."""
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {project_id}")

    # Try worker session workspace first
    if project.worker_session_id:
        session = db.get(Session, project.worker_session_id)
        if session and session.workspace_path:
            ws = Path(session.workspace_path)
            if ws.is_dir():
                return ws

    # Fallback: check project repo_path
    if project.repo_path:
        repo = Path(project.repo_path)
        if repo.is_dir():
            return repo

    raise NotFoundError(f"No workspace found for project {project_id}")


def _safe_resolve(workspace: Path, rel_path: str) -> Path:
    """Resolve a relative path inside the workspace, preventing traversal."""
    resolved = (workspace / rel_path).resolve()
    if not str(resolved).startswith(str(workspace.resolve())):
        raise ValidationError("Path traversal not allowed")
    return resolved


@router.get("", response_model=list[FileEntry])
def list_files(
    project_id: UUID,
    path: str = Query(default="", description="Relative directory path"),
    db: DBSession = Depends(get_db),
) -> list[FileEntry]:
    workspace = _resolve_workspace(project_id, db)
    target = _safe_resolve(workspace, path) if path else workspace

    if not target.is_dir():
        raise NotFoundError(f"Directory not found: {path}")

    entries: list[FileEntry] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        # Skip hidden files and .git
        if child.name.startswith("."):
            continue
        rel = str(child.relative_to(workspace))
        entries.append(FileEntry(
            name=child.name,
            path=rel,
            is_dir=child.is_dir(),
            size=child.stat().st_size if child.is_file() else None,
        ))
    return entries


@router.get("/content", response_model=FileContent)
def read_file(
    project_id: UUID,
    path: str = Query(description="Relative file path"),
    db: DBSession = Depends(get_db),
) -> FileContent:
    workspace = _resolve_workspace(project_id, db)
    target = _safe_resolve(workspace, path)

    if not target.is_file():
        raise NotFoundError(f"File not found: {path}")

    suffix = target.suffix.lower()
    if suffix not in _TEXT_SUFFIXES:
        raise ValidationError(f"Cannot serve binary file: {target.name}")

    size = target.stat().st_size
    if size > MAX_FILE_SIZE:
        raise ValidationError(f"File too large ({size} bytes, max {MAX_FILE_SIZE})")

    content = target.read_text(encoding="utf-8", errors="replace")
    mime = mimetypes.guess_type(target.name)[0] or "text/plain"

    return FileContent(
        path=str(target.relative_to(workspace)),
        content=content,
        size=size,
        mime_type=mime,
    )


# ── Raw file serving for iframe preview ──


_SERVABLE_SUFFIXES = {
    ".html", ".css", ".js", ".json", ".svg", ".png", ".jpg", ".jpeg",
    ".gif", ".ico", ".woff", ".woff2", ".ttf", ".webp",
}


@router.get("/raw/{file_path:path}")
def serve_raw(
    project_id: UUID,
    file_path: str,
    db: DBSession = Depends(get_db),
) -> Response:
    """Serve a file with its native content-type. Used for iframe previews."""
    workspace = _resolve_workspace(project_id, db)
    target = _safe_resolve(workspace, file_path)

    if not target.is_file():
        # Try index.html for directory requests
        index = target / "index.html" if target.is_dir() else None
        if index and index.is_file():
            target = index
        else:
            raise NotFoundError(f"File not found: {file_path}")

    suffix = target.suffix.lower()
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"

    # For HTML, rewrite asset paths so they resolve through the API
    if suffix == ".html":
        import re
        content = target.read_text(encoding="utf-8", errors="replace")
        raw_base = f"/api/v1/projects/{project_id}/files/raw"

        # Determine the directory context for relative paths
        parent_dir = str(Path(file_path).parent)
        if parent_dir == ".":
            parent_dir = ""

        # Rewrite asset paths so they resolve through the raw file API.
        # Absolute paths like /styles.css become relative to the HTML's parent dir
        # (because the original app server serves from that dir as document root).
        def rewrite_path(match: re.Match) -> str:
            attr = match.group(1)  # href= or src=
            quote = match.group(2)  # " or '
            path_val = match.group(3)
            # Skip external URLs, data URIs, anchors, protocol-relative
            if path_val.startswith(("http://", "https://", "data:", "//", "#", "mailto:")):
                return match.group(0)
            # Absolute path from root — treat parent_dir as the document root
            if path_val.startswith("/"):
                asset = path_val.lstrip("/")
                if parent_dir:
                    return f'{attr}{quote}{raw_base}/{parent_dir}/{asset}{quote}'
                return f'{attr}{quote}{raw_base}/{asset}{quote}'
            # Relative path — resolve against current directory
            if parent_dir:
                return f'{attr}{quote}{raw_base}/{parent_dir}/{path_val}{quote}'
            return f'{attr}{quote}{raw_base}/{path_val}{quote}'

        content = re.sub(
            r'((?:href|src|action)\s*=\s*)(["\'])([^"\']*?)\2',
            rewrite_path,
            content,
            flags=re.IGNORECASE,
        )
        return HTMLResponse(content=content)

    return FileResponse(
        path=str(target),
        media_type=mime,
    )


# ── Preview detection ──


class PreviewInfo(BaseModel):
    available: bool
    entry_file: str | None = None
    preview_url: str | None = None
    kind: str | None = None  # "html", "markdown", "none"


@router.get("/preview-info", response_model=PreviewInfo)
def get_preview_info(
    project_id: UUID,
    db: DBSession = Depends(get_db),
) -> PreviewInfo:
    """Detect if this project has a previewable entry point."""
    workspace = _resolve_workspace(project_id, db)

    # Check for index.html first
    for candidate in ["index.html", "public/index.html", "dist/index.html", "build/index.html"]:
        if (workspace / candidate).is_file():
            return PreviewInfo(
                available=True,
                entry_file=candidate,
                preview_url=f"/api/v1/projects/{project_id}/files/raw/{candidate}",
                kind="html",
            )

    # Check for any .html file
    html_files = list(workspace.glob("*.html"))
    if html_files:
        rel = str(html_files[0].relative_to(workspace))
        return PreviewInfo(
            available=True,
            entry_file=rel,
            preview_url=f"/api/v1/projects/{project_id}/files/raw/{rel}",
            kind="html",
        )

    # Check for README.md as rendered preview
    for candidate in ["README.md", "readme.md"]:
        if (workspace / candidate).is_file():
            return PreviewInfo(
                available=True,
                entry_file=candidate,
                preview_url=f"/api/v1/projects/{project_id}/files/raw/{candidate}",
                kind="markdown",
            )

    return PreviewInfo(available=False, kind="none")


# ── GitHub push ──


class GitHubPushRequest(BaseModel):
    repo_name: str | None = None  # defaults to project slug
    private: bool = True
    org: str | None = None  # push to org instead of personal


class GitHubPushResult(BaseModel):
    success: bool
    repo_url: str | None = None
    error: str | None = None


@router.post("/push-github", response_model=GitHubPushResult)
def push_to_github(
    project_id: UUID,
    payload: GitHubPushRequest,
    db: DBSession = Depends(get_db),
) -> GitHubPushResult:
    """Create a GitHub repo and push workspace contents."""
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError(f"Project not found: {project_id}")

    workspace = _resolve_workspace(project_id, db)
    repo_name = payload.repo_name or project.slug

    if not shutil.which("gh"):
        return GitHubPushResult(success=False, error="GitHub CLI (gh) not installed")

    try:
        # Ensure workspace is a git repo
        if not (workspace / ".git").exists():
            subprocess.run(["git", "init", "-b", "main"], cwd=workspace, capture_output=True, check=True)
            subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"Initial deliverable: {project.name}"],
                cwd=workspace, capture_output=True, check=True,
                env={**__import__("os").environ, "GIT_AUTHOR_NAME": "Poulpe AI", "GIT_COMMITTER_NAME": "Poulpe AI",
                     "GIT_AUTHOR_EMAIL": "poulpe@local", "GIT_COMMITTER_EMAIL": "poulpe@local"},
            )
        else:
            # Stage and commit any uncommitted changes
            status = subprocess.run(["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True)
            if status.stdout.strip():
                subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True, check=True)
                subprocess.run(
                    ["git", "commit", "-m", f"Update deliverable: {project.name}"],
                    cwd=workspace, capture_output=True, check=True,
                    env={**__import__("os").environ, "GIT_AUTHOR_NAME": "Poulpe AI", "GIT_COMMITTER_NAME": "Poulpe AI",
                         "GIT_AUTHOR_EMAIL": "poulpe@local", "GIT_COMMITTER_EMAIL": "poulpe@local"},
                )

        # Create GitHub repo
        gh_cmd = ["gh", "repo", "create", repo_name, "--source", str(workspace), "--push"]
        if payload.private:
            gh_cmd.append("--private")
        else:
            gh_cmd.append("--public")
        if payload.org:
            # Prefix org to repo name
            gh_cmd[3] = f"{payload.org}/{repo_name}"

        result = subprocess.run(gh_cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            # If repo already exists, just push
            if "already exists" in stderr:
                remote_check = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    cwd=workspace, capture_output=True, text=True,
                )
                if remote_check.returncode != 0:
                    # Add remote
                    subprocess.run(
                        ["gh", "repo", "view", repo_name, "--json", "url", "-q", ".url"],
                        capture_output=True, text=True,
                    )
                    subprocess.run(
                        ["git", "remote", "add", "origin", f"https://github.com/{repo_name}.git"],
                        cwd=workspace, capture_output=True,
                    )
                push_result = subprocess.run(
                    ["git", "push", "-u", "origin", "main"],
                    cwd=workspace, capture_output=True, text=True, timeout=30,
                )
                if push_result.returncode != 0:
                    return GitHubPushResult(success=False, error=push_result.stderr.strip())
                # Get the repo URL
                url_result = subprocess.run(
                    ["gh", "repo", "view", repo_name, "--json", "url", "-q", ".url"],
                    capture_output=True, text=True,
                )
                return GitHubPushResult(success=True, repo_url=url_result.stdout.strip())
            return GitHubPushResult(success=False, error=stderr)

        # Extract repo URL from gh output
        repo_url = result.stdout.strip()
        if not repo_url.startswith("http"):
            url_result = subprocess.run(
                ["gh", "repo", "view", repo_name, "--json", "url", "-q", ".url"],
                capture_output=True, text=True,
            )
            repo_url = url_result.stdout.strip()

        return GitHubPushResult(success=True, repo_url=repo_url)

    except subprocess.TimeoutExpired:
        return GitHubPushResult(success=False, error="Push timed out after 30s")
    except Exception as e:
        return GitHubPushResult(success=False, error=str(e))
