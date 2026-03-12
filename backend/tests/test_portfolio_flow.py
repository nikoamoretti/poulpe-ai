from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID

from app.models.session import Session as SessionModel


def _wait_for_status(client, session_id: str, expected: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200
        payload = response.json()
        last_payload = payload
        if payload["status"] == expected:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"Session {session_id} did not reach {expected}. Last payload: {last_payload}")


def _wait_for_checkpoint(
    client,
    portfolio_id: str,
    kind: str,
    status: str = "open",
    timeout_seconds: float = 5.0,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: list[dict] | None = None
    while time.time() < deadline:
        response = client.get(
            f"/api/v1/portfolios/{portfolio_id}/inbox",
            params={"status": status},
        )
        assert response.status_code == 200
        payload = response.json()
        last_payload = payload
        for item in payload:
            if item["kind"] == kind:
                return item
        time.sleep(0.1)
    raise AssertionError(f"Checkpoint kind={kind} status={status} not found. Last payload: {last_payload}")


def _automation_action(actions: list[dict], kind: str) -> dict:
    for action in actions:
        if action["kind"] == kind:
            return action
    raise AssertionError(f"Automation action {kind} not found in {actions}")


def _tick_until_action(
    client,
    portfolio_id: str,
    kind: str,
    timeout_seconds: float = 5.0,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_actions: list[dict] | None = None
    while time.time() < deadline:
        response = client.post(f"/api/v1/portfolios/{portfolio_id}/automation/tick")
        assert response.status_code == 200
        actions = response.json()["actions"]
        last_actions = actions
        for action in actions:
            if action["kind"] == kind:
                return action
        time.sleep(0.1)
    raise AssertionError(f"Automation action {kind} not found. Last actions: {last_actions}")


def test_portfolio_manager_and_project_worker_flow(client, git_repo: Path) -> None:
    portfolio_response = client.post(
        "/api/v1/portfolios",
        json={
            "name": "Rail Portfolio",
            "goal": "Coordinate multiple independent repo efforts.",
        },
    )
    assert portfolio_response.status_code == 201
    portfolio = portfolio_response.json()

    project_response = client.post(
        "/api/v1/projects",
        json={
            "portfolio_id": portfolio["id"],
            "name": "Project Alpha",
            "repo_path": str(git_repo),
            "objective": "Inspect the repo and prepare the first implementation steps.",
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()
    assert project["portfolio_id"] == portfolio["id"]
    assert project["objective"] == "Inspect the repo and prepare the first implementation steps."

    list_response = client.get("/api/v1/projects", params={"portfolio_id": portfolio["id"]})
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [project["id"]]

    manager_start = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/manager/start",
        json={
            "simulation_mode": True,
            "initial_message": "Supervise this portfolio and answer project questions.",
        },
    )
    assert manager_start.status_code == 200
    manager_session = manager_start.json()
    assert manager_session["portfolio_id"] == portfolio["id"]
    assert manager_session["project_id"] is None
    assert Path(manager_session["workspace_path"]).exists()
    _wait_for_status(client, manager_session["id"], "running")

    project_start = client.post(
        f"/api/v1/projects/{project['id']}/start",
        json={
            "simulation_mode": True,
            "initial_message": "Begin work and share progress as you go.",
        },
    )
    assert project_start.status_code == 200
    worker_session = project_start.json()
    assert worker_session["portfolio_id"] == portfolio["id"]
    assert worker_session["project_id"] == project["id"]
    assert worker_session["task_id"] is None
    assert worker_session["runtime"]["simulated"] is True
    running_worker = _wait_for_status(client, worker_session["id"], "running")
    assert Path(running_worker["workspace_path"]).exists()

    project_read = client.get(f"/api/v1/projects/{project['id']}").json()
    assert project_read["worker_session_id"] == worker_session["id"]

    stop_worker = client.post(f"/api/v1/sessions/{worker_session['id']}/stop")
    assert stop_worker.status_code == 200
    _wait_for_status(client, worker_session["id"], "stopped")

    stop_manager = client.post(f"/api/v1/sessions/{manager_session['id']}/stop")
    assert stop_manager.status_code == 200
    _wait_for_status(client, manager_session["id"], "stopped")


def test_project_can_auto_create_repo_from_name(client) -> None:
    portfolio_response = client.post(
        "/api/v1/portfolios",
        json={
            "name": "Bootstrap Portfolio",
            "goal": "Spin up new repos from project names.",
        },
    )
    assert portfolio_response.status_code == 201
    portfolio = portfolio_response.json()

    project_response = client.post(
        "/api/v1/projects",
        json={
            "portfolio_id": portfolio["id"],
            "name": "Greenfield API",
            "create_repo": True,
            "objective": "Create the initial service repository and bootstrap the first commit.",
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()

    repo_path = Path(project["repo_path"])
    assert repo_path.exists()
    assert repo_path.name == "greenfield-api"
    assert (repo_path / ".git").exists()
    readme_path = repo_path / "README.md"
    assert readme_path.exists()
    assert "Greenfield API" in readme_path.read_text(encoding="utf-8")

    head = client.app.state.container.command_runner.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        check=False,
    )
    assert head.returncode == 0
    assert head.stdout.strip()


def test_project_name_only_creation_uses_default_objective(client) -> None:
    portfolio_response = client.post(
        "/api/v1/portfolios",
        json={
            "name": "Name Only Portfolio",
            "goal": "Allow quick project creation with only a name.",
        },
    )
    assert portfolio_response.status_code == 201
    portfolio = portfolio_response.json()

    project_response = client.post(
        "/api/v1/projects",
        json={
            "portfolio_id": portfolio["id"],
            "name": "Name Only Project",
            "create_repo": True,
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()
    assert project["objective"] == "Work independently on Name Only Project and bring it to completion."

    readme_path = Path(project["repo_path"]) / "README.md"
    assert readme_path.exists()
    assert "Work independently on Name Only Project and bring it to completion." in readme_path.read_text(
        encoding="utf-8"
    )


def test_portfolio_question_checkpoint_can_be_answered(client, git_repo: Path) -> None:
    portfolio = client.post(
        "/api/v1/portfolios",
        json={"name": "Question Portfolio", "goal": "Answer project questions quickly."},
    ).json()
    manager_session = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/manager/start",
        json={"simulation_mode": True, "initial_message": "Manage the portfolio."},
    ).json()
    _wait_for_status(client, manager_session["id"], "running")

    project = client.post(
        "/api/v1/projects",
        json={
            "portfolio_id": portfolio["id"],
            "name": "Question Project",
            "repo_path": str(git_repo),
            "objective": "Inspect the repo and wait for manager guidance.",
        },
    ).json()
    worker_session = client.post(
        f"/api/v1/projects/{project['id']}/start",
        json={"simulation_mode": True},
    ).json()
    worker_session = _wait_for_status(client, worker_session["id"], "running")
    assert worker_session["supervisor_session_id"] == manager_session["id"]

    send_question = client.post(
        f"/api/v1/sessions/{worker_session['id']}/messages",
        json={"message": "question"},
    )
    assert send_question.status_code == 200
    _wait_for_status(client, worker_session["id"], "blocked")

    checkpoint = _wait_for_checkpoint(client, portfolio["id"], "question")
    assert checkpoint["manager_session_id"] == manager_session["id"]
    assert checkpoint["project_id"] == project["id"]

    answer = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/inbox/{checkpoint['id']}/respond",
        json={
            "action": "answer",
            "message": "Do not update the schema. Keep the response shape unchanged and continue.",
        },
    )
    assert answer.status_code == 200
    answered = answer.json()
    assert answered["status"] == "resolved"
    assert answered["resolution"] == "answered"

    running_again = _wait_for_status(client, worker_session["id"], "running")
    assert running_again["blocked_reason"] is None

    open_items = client.get(f"/api/v1/portfolios/{portfolio['id']}/inbox").json()
    assert open_items == []


def test_portfolio_completion_checkpoint_can_request_changes_and_restart_worker(client, git_repo: Path) -> None:
    portfolio = client.post(
        "/api/v1/portfolios",
        json={"name": "Changes Portfolio", "goal": "Review project completion claims."},
    ).json()
    manager_session = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/manager/start",
        json={"simulation_mode": True, "initial_message": "Review completions."},
    ).json()
    _wait_for_status(client, manager_session["id"], "running")

    project = client.post(
        "/api/v1/projects",
        json={
            "portfolio_id": portfolio["id"],
            "name": "Changes Project",
            "repo_path": str(git_repo),
            "objective": "Inspect the repo and prepare a first implementation pass.",
        },
    ).json()
    first_worker = client.post(
        f"/api/v1/projects/{project['id']}/start",
        json={"simulation_mode": True},
    ).json()
    running_first_worker = _wait_for_status(client, first_worker["id"], "running")
    assert running_first_worker["workspace_path"] is not None
    (Path(running_first_worker["workspace_path"]) / "README.md").write_text(
        "# Sample Repo\n\nRevised before manager review.\n",
        encoding="utf-8",
    )

    complete = client.post(
        f"/api/v1/sessions/{first_worker['id']}/messages",
        json={"message": "complete"},
    )
    assert complete.status_code == 200
    _wait_for_status(client, first_worker["id"], "completed")

    checkpoint = _wait_for_checkpoint(client, portfolio["id"], "completion")
    assert checkpoint["details"]["review_context"]["diff"]["file_count"] == 1
    assert checkpoint["details"]["review_context"]["diff"]["changed_files"] == ["README.md"]
    assert checkpoint["artifacts"]
    diff_artifact = next(artifact for artifact in checkpoint["artifacts"] if artifact["kind"] == "diff")
    assert diff_artifact["metadata"]["summary"]["file_count"] == 1
    assert "Revised before manager review." in diff_artifact["metadata"]["diff"]

    request_changes = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/inbox/{checkpoint['id']}/respond",
        json={
            "action": "request_changes",
            "message": "Make one more pass and confirm the final behavior before approval.",
        },
    )
    assert request_changes.status_code == 200
    resolved = request_changes.json()
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "changes_requested"

    project_read = client.get(f"/api/v1/projects/{project['id']}").json()
    assert project_read["worker_session_id"] != first_worker["id"]
    assert project_read["completion_summary"] is None

    replacement_worker = _wait_for_status(client, project_read["worker_session_id"], "running")
    assert replacement_worker["supervisor_session_id"] == manager_session["id"]
    assert resolved["response_details"]["routed_worker_session_id"] == replacement_worker["id"]


def test_portfolio_completion_checkpoint_can_be_approved(client, git_repo: Path) -> None:
    portfolio = client.post(
        "/api/v1/portfolios",
        json={"name": "Approval Portfolio", "goal": "Approve good work."},
    ).json()
    client.post(
        f"/api/v1/portfolios/{portfolio['id']}/manager/start",
        json={"simulation_mode": True, "initial_message": "Approve finished projects."},
    )

    project = client.post(
        "/api/v1/projects",
        json={
            "portfolio_id": portfolio["id"],
            "name": "Approval Project",
            "repo_path": str(git_repo),
            "objective": "Inspect the repo and prepare a clean implementation pass.",
        },
    ).json()
    worker = client.post(
        f"/api/v1/projects/{project['id']}/start",
        json={"simulation_mode": True},
    ).json()
    _wait_for_status(client, worker["id"], "running")

    complete = client.post(
        f"/api/v1/sessions/{worker['id']}/messages",
        json={"message": "complete"},
    )
    assert complete.status_code == 200
    _wait_for_status(client, worker["id"], "completed")

    checkpoint = _wait_for_checkpoint(client, portfolio["id"], "completion")
    approve = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/inbox/{checkpoint['id']}/respond",
        json={
            "action": "approve",
            "message": "Approved for the current portfolio milestone.",
        },
    )
    assert approve.status_code == 200
    approved = approve.json()
    assert approved["status"] == "resolved"
    assert approved["resolution"] == "approved"

    project_read = client.get(f"/api/v1/projects/{project['id']}").json()
    assert project_read["completion_summary"] == "Approved for the current portfolio milestone."


def test_portfolio_automation_answers_question_checkpoint(client, git_repo: Path) -> None:
    portfolio = client.post(
        "/api/v1/portfolios",
        json={"name": "Auto Answer Portfolio", "goal": "Automatically answer project blockers."},
    ).json()
    client.post(
        f"/api/v1/portfolios/{portfolio['id']}/manager/start",
        json={"simulation_mode": True, "initial_message": "Manage the portfolio automatically."},
    )

    project = client.post(
        "/api/v1/projects",
        json={
            "portfolio_id": portfolio["id"],
            "name": "Auto Answer Project",
            "repo_path": str(git_repo),
            "objective": "Inspect the repo and ask for clarification when needed.",
        },
    ).json()
    worker = client.post(
        f"/api/v1/projects/{project['id']}/start",
        json={"simulation_mode": True},
    ).json()
    _wait_for_status(client, worker["id"], "running")

    ask = client.post(
        f"/api/v1/sessions/{worker['id']}/messages",
        json={"message": "question"},
    )
    assert ask.status_code == 200
    _wait_for_status(client, worker["id"], "blocked")
    checkpoint = _wait_for_checkpoint(client, portfolio["id"], "question")

    first_tick = client.post(f"/api/v1/portfolios/{portfolio['id']}/automation/tick")
    assert first_tick.status_code == 200
    first_actions = first_tick.json()["actions"]
    turn_action = _automation_action(first_actions, "manager_turn_started")
    _wait_for_status(client, turn_action["session_id"], "completed")

    _tick_until_action(client, portfolio["id"], "manager_turn_resolved")

    resolved_checkpoint = client.get(
        f"/api/v1/portfolios/{portfolio['id']}/inbox",
        params={"status": "resolved"},
    ).json()
    assert resolved_checkpoint[0]["id"] == checkpoint["id"]
    assert resolved_checkpoint[0]["resolution"] == "answered"
    assert resolved_checkpoint[0]["response_details"]["automation"]["result"] == "answer"
    _wait_for_status(client, worker["id"], "running")


def test_portfolio_automation_approves_completion_checkpoint(client, git_repo: Path) -> None:
    portfolio = client.post(
        "/api/v1/portfolios",
        json={"name": "Auto Approval Portfolio", "goal": "Automatically review completion claims."},
    ).json()
    client.post(
        f"/api/v1/portfolios/{portfolio['id']}/manager/start",
        json={"simulation_mode": True, "initial_message": "Manage the portfolio automatically."},
    )

    project = client.post(
        "/api/v1/projects",
        json={
            "portfolio_id": portfolio["id"],
            "name": "Auto Approval Project",
            "repo_path": str(git_repo),
            "objective": "Make one concrete change and report completion.",
        },
    ).json()
    worker = client.post(
        f"/api/v1/projects/{project['id']}/start",
        json={"simulation_mode": True},
    ).json()
    running_worker = _wait_for_status(client, worker["id"], "running")
    workspace_path = Path(running_worker["workspace_path"])
    (workspace_path / "README.md").write_text("# Sample Repo\n\nApproved by automation.\n", encoding="utf-8")

    complete = client.post(
        f"/api/v1/sessions/{worker['id']}/messages",
        json={"message": "complete"},
    )
    assert complete.status_code == 200
    _wait_for_status(client, worker["id"], "completed")
    checkpoint = _wait_for_checkpoint(client, portfolio["id"], "completion")
    assert checkpoint["details"]["review_context"]["diff"]["file_count"] == 1

    first_tick = client.post(f"/api/v1/portfolios/{portfolio['id']}/automation/tick")
    assert first_tick.status_code == 200
    turn_action = _automation_action(first_tick.json()["actions"], "manager_turn_started")
    _wait_for_status(client, turn_action["session_id"], "completed")

    _tick_until_action(client, portfolio["id"], "manager_turn_resolved")

    project_read = client.get(f"/api/v1/projects/{project['id']}").json()
    assert project_read["completion_summary"] == "Approved for the current project milestone."
    resolved_checkpoint = client.get(
        f"/api/v1/portfolios/{portfolio['id']}/inbox",
        params={"status": "resolved"},
    ).json()
    assert resolved_checkpoint[0]["id"] == checkpoint["id"]
    assert resolved_checkpoint[0]["resolution"] == "approved"


def test_real_runtime_manager_reply_restarts_worker_turn_in_same_workspace(client, git_repo: Path) -> None:
    portfolio = client.post(
        "/api/v1/portfolios",
        json={"name": "Turn Portfolio", "goal": "Use turn-based worker supervision."},
    ).json()
    manager_session = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/manager/start",
        json={"simulation_mode": True, "initial_message": "Manage the portfolio."},
    ).json()
    _wait_for_status(client, manager_session["id"], "running")

    project = client.post(
        "/api/v1/projects",
        json={
            "portfolio_id": portfolio["id"],
            "name": "Turn Project",
            "repo_path": str(git_repo),
            "objective": "Inspect the repo and keep working through manager turns.",
        },
    ).json()
    worker = client.post(
        f"/api/v1/projects/{project['id']}/start",
        json={"simulation_mode": True},
    ).json()
    running_worker = _wait_for_status(client, worker["id"], "running")
    original_workspace_path = running_worker["workspace_path"]

    with client.app.state.container.database.session() as db:
        session = db.get(SessionModel, UUID(worker["id"]))
        assert session is not None
        metadata = dict(session.metadata_json)
        runtime = dict(metadata.get("runtime") or {})
        runtime.update(
            {
                "requested_provider": "codex",
                "resolved_provider": "codex",
                "simulated": False,
                "available": True,
                "configured": True,
                "disconnected": False,
                "can_start": True,
                "summary": "Using a real Codex process.",
            }
        )
        metadata["runtime"] = runtime
        session.metadata_json = metadata
        db.commit()

    response = client.post(
        f"/api/v1/projects/{project['id']}/manager-instructions",
        json={
            "message": "Continue from the existing workspace and make one more pass.",
            "metadata": {"origin": "manager-turn"},
        },
    )
    assert response.status_code == 200
    replacement_worker = response.json()
    assert replacement_worker["id"] != worker["id"]
    assert replacement_worker["workspace_path"] == original_workspace_path

    _wait_for_status(client, worker["id"], "stopped")
    _wait_for_status(client, replacement_worker["id"], "running")

    project_read = client.get(f"/api/v1/projects/{project['id']}").json()
    assert project_read["worker_session_id"] == replacement_worker["id"]
