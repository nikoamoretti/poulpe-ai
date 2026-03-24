"""Deploy Service — pushes code to GitHub and deploys to Vercel.

Handles the full pipeline: git init → commit → push to GitHub → Vercel deploy.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DeployService:
    """Deploys business products to GitHub + Vercel."""

    def __init__(self) -> None:
        self.github_token = os.environ.get("GITHUB_TOKEN", "")
        self.github_org = os.environ.get("GITHUB_ORG", "")
        self.vercel_token = os.environ.get("VERCEL_TOKEN", "")
        self.vercel_team_id = os.environ.get("VERCEL_TEAM_ID")

    def deploy_workspace(
        self,
        workspace_path: str,
        repo_name: str,
        *,
        private: bool = True,
    ) -> dict[str, Any]:
        """Full pipeline: init git → push to GitHub → deploy to Vercel."""
        results: dict[str, Any] = {"workspace": workspace_path, "repo_name": repo_name}

        # 1. Git init and commit
        git_result = self._git_init_and_push(workspace_path, repo_name, private=private)
        results["git"] = git_result
        if git_result.get("status") == "error":
            return results

        # 2. Deploy to Vercel
        github_repo = git_result.get("full_name", "")
        if github_repo and self.vercel_token:
            vercel_result = self._vercel_deploy(repo_name, github_repo)
            results["vercel"] = vercel_result
        else:
            results["vercel"] = {"status": "skipped", "reason": "No VERCEL_TOKEN or no GitHub repo"}

        return results

    def _git_init_and_push(
        self,
        workspace_path: str,
        repo_name: str,
        *,
        private: bool = True,
    ) -> dict[str, Any]:
        """Initialize git, commit, create GitHub repo, and push."""
        ws = Path(workspace_path)
        if not ws.exists():
            return {"status": "error", "reason": f"Workspace not found: {workspace_path}"}

        if not self.github_token:
            return {"status": "error", "reason": "GITHUB_TOKEN not set"}

        try:
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

            # Init if needed
            git_dir = ws / ".git"
            if not git_dir.exists():
                self._run(["git", "init"], cwd=ws, env=env)
                self._run(["git", "checkout", "-b", "main"], cwd=ws, env=env)

            # Create .gitignore if missing
            gitignore = ws / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(
                    "node_modules/\n.next/\n__pycache__/\n.venv/\n.env\n.env.local\n*.pyc\n.DS_Store\n",
                    encoding="utf-8",
                )

            # Stage and commit
            self._run(["git", "add", "-A"], cwd=ws, env=env)

            # Check if there are changes to commit
            status = self._run(["git", "status", "--porcelain"], cwd=ws, env=env)
            if status.strip():
                self._run(
                    ["git", "commit", "-m", "Autonomous build — deployed by Poulpe Business Agent"],
                    cwd=ws,
                    env=env,
                )

            # Create GitHub repo
            repo_result = self._create_github_repo(repo_name, private=private)
            if repo_result.get("status") == "error":
                return repo_result

            clone_url = repo_result.get("clone_url", "")
            full_name = repo_result.get("full_name", "")

            # Set remote and push
            remotes = self._run(["git", "remote"], cwd=ws, env=env)
            if "origin" in remotes:
                self._run(["git", "remote", "set-url", "origin", clone_url], cwd=ws, env=env)
            else:
                self._run(["git", "remote", "add", "origin", clone_url], cwd=ws, env=env)

            # Push with token auth
            auth_url = clone_url.replace(
                "https://", f"https://x-access-token:{self.github_token}@"
            )
            self._run(["git", "push", "-u", auth_url, "main"], cwd=ws, env=env)

            return {
                "status": "pushed",
                "full_name": full_name,
                "clone_url": clone_url,
                "html_url": repo_result.get("html_url", ""),
            }

        except Exception as exc:
            logger.exception("git init/push failed for %s", workspace_path)
            return {"status": "error", "reason": str(exc)}

    def _create_github_repo(
        self, name: str, *, private: bool = True
    ) -> dict[str, Any]:
        """Create a GitHub repo via API. Returns repo info or existing repo."""
        try:
            with httpx.Client(
                base_url="https://api.github.com",
                headers={
                    "Authorization": f"token {self.github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                timeout=15.0,
            ) as client:
                # Try creating
                if self.github_org:
                    endpoint = f"/orgs/{self.github_org}/repos"
                else:
                    endpoint = "/user/repos"

                resp = client.post(endpoint, json={
                    "name": name,
                    "private": private,
                    "auto_init": False,
                })

                if resp.status_code == 422:
                    # Repo already exists — fetch it
                    owner = self.github_org or self._get_github_username(client)
                    resp = client.get(f"/repos/{owner}/{name}")
                    resp.raise_for_status()

                elif resp.status_code >= 400:
                    return {"status": "error", "reason": f"GitHub API: {resp.status_code} {resp.text[:200]}"}

                data = resp.json()
                return {
                    "status": "ok",
                    "full_name": data.get("full_name", ""),
                    "clone_url": data.get("clone_url", ""),
                    "html_url": data.get("html_url", ""),
                }
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    def _get_github_username(self, client: httpx.Client) -> str:
        resp = client.get("/user")
        resp.raise_for_status()
        return resp.json().get("login", "")

    def _get_github_repo_id(self, github_repo: str) -> int | None:
        """Fetch the numeric GitHub repo ID for use in Vercel gitSource."""
        try:
            with httpx.Client(
                base_url="https://api.github.com",
                headers={
                    "Authorization": f"token {self.github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                timeout=10.0,
            ) as client:
                resp = client.get(f"/repos/{github_repo}")
                resp.raise_for_status()
                return resp.json().get("id")
        except Exception:
            logger.warning("Could not fetch GitHub repo ID for %s", github_repo)
            return None

    def _vercel_deploy(
        self, project_name: str, github_repo: str
    ) -> dict[str, Any]:
        """Create or link a Vercel project from a GitHub repo."""
        try:
            params: dict[str, str] = {}
            if self.vercel_team_id:
                params["teamId"] = self.vercel_team_id

            with httpx.Client(
                base_url="https://api.vercel.com",
                headers={"Authorization": f"Bearer {self.vercel_token}"},
                timeout=30.0,
            ) as client:
                # Create project linked to GitHub repo
                owner, repo = github_repo.split("/", 1)
                resp = client.post("/v10/projects", json={
                    "name": project_name,
                    "gitRepository": {
                        "type": "github",
                        "repo": github_repo,
                    },
                    "framework": "nextjs",
                }, params=params)

                if resp.status_code == 409:
                    # Project exists, just trigger redeploy
                    # Vercel v13 deployments API requires repoId (numeric GitHub repo ID)
                    repo_id = self._get_github_repo_id(github_repo)
                    deploy_payload: dict[str, Any] = {
                        "name": project_name,
                        "gitSource": {
                            "type": "github",
                            "repo": github_repo,
                            "ref": "main",
                        },
                    }
                    if repo_id:
                        deploy_payload["gitSource"]["repoId"] = repo_id
                    resp = client.post("/v13/deployments", json=deploy_payload, params=params)

                if resp.status_code >= 400:
                    return {"status": "error", "reason": f"Vercel API: {resp.status_code} {resp.text[:200]}"}

                data = resp.json()
                deploy_url = data.get("url") or data.get("alias", [{}])[0].get("domain", "")

                return {
                    "status": "deployed",
                    "url": f"https://{deploy_url}" if deploy_url else "",
                    "project_id": data.get("id", ""),
                }

        except Exception as exc:
            logger.exception("Vercel deploy failed")
            return {"status": "error", "reason": str(exc)}

    @staticmethod
    def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> str:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            stderr = result.stderr.strip()
            if stderr and "warning" not in stderr.lower():
                logger.warning("git command %s stderr: %s", cmd, stderr[:200])
        return result.stdout
