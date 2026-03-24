"""Business Orchestration Service — the autonomous execution engine.

Handles the full lifecycle of business daily cycles:
1. Pending cycles → spawn CEO agent session
2. Running cycles → monitor CEO session completion
3. CEO plan ready → delegate tasks to engineer/research/marketing agents
4. All agents done → mark cycle complete, send digest
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.adapters.claude_api_adapter import ClaudeAPIAdapter, ClaudeAPIResponse
from app.core.config import Settings
from app.core.database import DatabaseManager
from app.core.enums import (
    BusinessCycleStatus,
    BusinessStatus,
    EventCategory,
    EventLevel,
    SessionRole,
    SessionStatus,
    StructuredEventType,
)
from app.core.errors import NotFoundError
from app.core.event_stream import EventStreamBroker
from app.adapters.redis_bus import RedisBusAdapter
from app.adapters.repo_inspector import RepoInspectorAdapter
from app.models.business import Business
from app.models.business_cycle import BusinessCycle
from app.models.parsed_session_event import ParsedSessionEvent
from app.models.session import Session as SessionModel
from app.schemas.event import EventCreate, EventSourceRef
from app.services.business_cycle_service import BusinessCycleService
from app.services.command_runner import CommandRunner
from app.services.deploy_service import DeployService
from app.services.digest_service import DigestService
from app.services.event_service import EventService
from app.services.outbound_service import OutboundService
from app.services.runtime_service import RuntimeService
from app.services.session_supervisor import SessionSupervisor
from app.services.task_packet_service import TaskPacketService
from app.services.worktree_manager import WorktreeManager

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ScopedServices:
    """Per-tick service instances sharing a DB session."""

    event_service: EventService
    cycle_service: BusinessCycleService
    digest_service: DigestService


class BusinessOrchestrationService:
    """Drives autonomous business cycles end-to-end.

    Called periodically from the background loop in main.py.
    Each call to ``tick_all()`` checks all active businesses and
    advances their cycles through the state machine.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        database: DatabaseManager,
        redis_bus: RedisBusAdapter,
        event_broker: EventStreamBroker,
        session_supervisor: SessionSupervisor,
        task_packet_service: TaskPacketService,
        runtime_service: RuntimeService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.redis_bus = redis_bus
        self.event_broker = event_broker
        self.session_supervisor = session_supervisor
        self.task_packet_service = task_packet_service
        self.runtime_service = runtime_service
        self._prompts_dir = Path(__file__).resolve().parents[1] / "prompts"

    # ── public entry point ──────────────────────────────────────────

    def tick_all(self) -> None:
        """Advance every active business's cycle. Safe to call repeatedly."""
        with self.database.session() as db:
            businesses = db.scalars(
                select(Business).where(Business.status == BusinessStatus.ACTIVE)
            ).all()
            business_ids = [b.id for b in businesses]

        for bid in business_ids:
            try:
                self._tick_business(bid)
            except Exception:
                logger.exception("business orchestration tick failed for %s", bid)

    # ── per-business tick ───────────────────────────────────────────

    def _tick_business(self, business_id: UUID) -> None:
        with self.database.session() as db:
            services = self._services(db)
            business = db.get(Business, business_id)
            if business is None or business.status != BusinessStatus.ACTIVE:
                return

            today = datetime.now(UTC).strftime("%Y-%m-%d")

            # Find today's cycle
            cycle = db.scalar(
                select(BusinessCycle).where(
                    and_(
                        BusinessCycle.business_id == business_id,
                        BusinessCycle.cycle_date == today,
                    )
                )
            )
            if cycle is None:
                return  # No cycle to process; cron hasn't fired yet

            if cycle.status == BusinessCycleStatus.PENDING:
                self._handle_pending_cycle(db, services, business, cycle)
            elif cycle.status == BusinessCycleStatus.RUNNING:
                self._handle_running_cycle(db, services, business, cycle)
            # COMPLETED and FAILED are terminal — nothing to do

    # ── state handlers ──────────────────────────────────────────────

    def _handle_pending_cycle(
        self,
        db: Session,
        services: _ScopedServices,
        business: Business,
        cycle: BusinessCycle,
    ) -> None:
        """Spawn a CEO agent session for a pending cycle."""
        logger.info(
            "spawning CEO session for business %s cycle %s",
            business.name,
            cycle.cycle_date,
        )

        launch_plan = self.session_supervisor.plan_session(
            role=SessionRole.MANAGER,
            runtime_preference="claude_code",
            allow_simulation_fallback=True,
        )

        workspace_path = str(
            Path(self.settings.orchestrator_workspaces_root / f"business-{business.slug}").resolve()
        )
        Path(workspace_path).mkdir(parents=True, exist_ok=True)

        metadata: dict[str, Any] = {
            "session_kind": "business_ceo_daily_cycle",
            "business_id": str(business.id),
            "cycle_id": str(cycle.id),
            "cycle_date": cycle.cycle_date,
            "simulation_mode": launch_plan.simulation_mode,
            "launch_notes": launch_plan.notes,
            "runtime": launch_plan.runtime.model_dump(mode="json"),
            "automation": {
                "business_id": str(business.id),
                "cycle_id": str(cycle.id),
                "launched_at": datetime.now(UTC).isoformat(),
            },
        }

        ceo_session = SessionModel(
            portfolio_id=business.portfolio_id,
            project_id=None,
            supervisor_session_id=None,
            role=SessionRole.MANAGER,
            status=launch_plan.initial_status,
            transport=launch_plan.transport,
            adapter_kind=launch_plan.adapter_kind,
            command=launch_plan.command,
            workspace_path=workspace_path,
            blocked_reason=launch_plan.blocked_reason,
            metadata_json=metadata,
            runtime_metadata_json={},
        )
        db.add(ceo_session)
        db.flush()
        db.commit()
        db.refresh(ceo_session)

        # Mark cycle as running
        cycle.status = BusinessCycleStatus.RUNNING
        cycle.ceo_session_id = ceo_session.id
        cycle.started_at = datetime.now(UTC)
        db.commit()

        # Start the session
        if ceo_session.status == SessionStatus.PENDING:
            try:
                self.session_supervisor.start_session(ceo_session.id)
            except Exception as exc:
                logger.exception(
                    "failed to start CEO session for business %s", business.id
                )
                cycle.status = BusinessCycleStatus.FAILED
                cycle.error_message = f"CEO session start failed: {exc}"
                cycle.completed_at = datetime.now(UTC)
                db.commit()
                return
        elif ceo_session.status == SessionStatus.BLOCKED:
            cycle.status = BusinessCycleStatus.FAILED
            cycle.error_message = f"CEO session blocked: {ceo_session.blocked_reason}"
            cycle.completed_at = datetime.now(UTC)
            db.commit()
            return

        services.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="business.ceo_session_started",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", id="business-orchestration"),
                session_id=ceo_session.id,
                payload={
                    "business_id": str(business.id),
                    "cycle_id": str(cycle.id),
                    "ceo_session_id": str(ceo_session.id),
                },
            )
        )

    def _handle_running_cycle(
        self,
        db: Session,
        services: _ScopedServices,
        business: Business,
        cycle: BusinessCycle,
    ) -> None:
        """Check if CEO session completed and process results."""
        if cycle.ceo_session_id is None:
            cycle.status = BusinessCycleStatus.FAILED
            cycle.error_message = "Running cycle has no CEO session"
            cycle.completed_at = datetime.now(UTC)
            db.commit()
            return

        ceo_session = db.get(SessionModel, cycle.ceo_session_id)
        if ceo_session is None:
            cycle.status = BusinessCycleStatus.FAILED
            cycle.error_message = "CEO session not found"
            cycle.completed_at = datetime.now(UTC)
            db.commit()
            return

        # Still running — wait
        if ceo_session.status in (
            SessionStatus.PENDING,
            SessionStatus.STARTING,
            SessionStatus.RUNNING,
        ):
            return

        # Failed or stopped
        if ceo_session.status in (SessionStatus.FAILED, SessionStatus.STOPPED):
            cycle.status = BusinessCycleStatus.FAILED
            cycle.error_message = (
                f"CEO session {ceo_session.status.value}: "
                f"{ceo_session.blocked_reason or 'unknown reason'}"
            )
            cycle.completed_at = datetime.now(UTC)
            db.commit()
            return

        # Completed — extract plan and delegate
        if ceo_session.status == SessionStatus.COMPLETED:
            self._process_ceo_completion(db, services, business, cycle, ceo_session)

    def _process_ceo_completion(
        self,
        db: Session,
        services: _ScopedServices,
        business: Business,
        cycle: BusinessCycle,
        ceo_session: SessionModel,
    ) -> None:
        """Extract the CEO's daily plan and delegate tasks."""
        # Find the last COMPLETE event from the CEO session
        complete_event = db.scalar(
            select(ParsedSessionEvent)
            .where(
                ParsedSessionEvent.session_id == ceo_session.id,
                ParsedSessionEvent.event_type == StructuredEventType.COMPLETE,
            )
            .order_by(ParsedSessionEvent.sequence.desc())
        )

        if complete_event is None:
            logger.warning(
                "CEO session %s completed without a COMPLETE event", ceo_session.id
            )
            cycle.status = BusinessCycleStatus.FAILED
            cycle.error_message = "CEO session completed without producing a plan"
            cycle.completed_at = datetime.now(UTC)
            db.commit()
            return

        payload = dict(complete_event.payload_json)
        result = str(payload.get("result", "")).strip().lower()

        if result != "daily_plan":
            logger.warning(
                "CEO session %s produced result=%r instead of daily_plan",
                ceo_session.id,
                result,
            )

        # Store the CEO plan
        cycle.ceo_plan = payload
        db.commit()

        # Delegate priorities to agents
        priorities = payload.get("priorities", [])
        agent_results: dict[str, Any] = {}

        for priority in priorities:
            if not isinstance(priority, dict):
                continue
            agent_type = str(priority.get("agent", "")).strip().lower()
            task_desc = str(priority.get("task", "")).strip()
            rank = priority.get("rank", 99)

            if not agent_type or not task_desc:
                continue

            try:
                if agent_type == "engineer":
                    result_data = self._delegate_to_engineer(
                        db, services, business, cycle, task_desc
                    )
                elif agent_type in ("researcher", "research"):
                    result_data = self._delegate_to_reasoning_agent(
                        business, task_desc, agent_type="researcher"
                    )
                elif agent_type in ("marketer", "marketing"):
                    result_data = self._delegate_to_reasoning_agent(
                        business, task_desc, agent_type="marketer"
                    )
                    # Execute outbound actions from marketing output
                    if result_data.get("status") == "completed":
                        outbound = OutboundService()
                        execution = outbound.execute_marketing_actions(
                            result_data, business.name
                        )
                        result_data["outbound_execution"] = execution
                else:
                    result_data = {"status": "skipped", "reason": f"Unknown agent type: {agent_type}"}

                agent_results[f"rank_{rank}_{agent_type}"] = {
                    "agent": agent_type,
                    "task": task_desc,
                    **result_data,
                }
            except Exception as exc:
                logger.exception(
                    "failed to delegate task to %s for business %s",
                    agent_type,
                    business.id,
                )
                agent_results[f"rank_{rank}_{agent_type}"] = {
                    "agent": agent_type,
                    "task": task_desc,
                    "status": "error",
                    "error": str(exc),
                }

        # Auto-deploy any completed engineer work
        for key, result in agent_results.items():
            if (
                result.get("agent") == "engineer"
                and result.get("status") == "delegated"
            ):
                try:
                    deploy_result = self._auto_deploy(business, result)
                    result["deploy"] = deploy_result
                except Exception:
                    logger.exception("auto-deploy failed for %s", key)
                    result["deploy"] = {"status": "error"}

        # Mark cycle complete
        cycle.agent_results = agent_results
        cycle.metrics_after = dict(business.metrics_snapshot)
        cycle.status = BusinessCycleStatus.COMPLETED
        cycle.completed_at = datetime.now(UTC)
        db.commit()

        services.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="business.cycle_completed",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", id="business-orchestration"),
                payload={
                    "business_id": str(business.id),
                    "cycle_id": str(cycle.id),
                    "priorities_count": len(priorities),
                    "agents_executed": len(agent_results),
                },
            )
        )

        # Send digest email
        self._send_digest(db, services, business, cycle)

        logger.info(
            "business cycle completed: %s (%s) — %d tasks delegated",
            business.name,
            cycle.cycle_date,
            len(agent_results),
        )

    # ── agent delegation ────────────────────────────────────────────

    def _delegate_to_engineer(
        self,
        db: Session,
        services: _ScopedServices,
        business: Business,
        cycle: BusinessCycle,
        task_description: str,
    ) -> dict[str, Any]:
        """Create a Poulpe project + worker session for an engineering task.

        The existing Poulpe worker system handles the actual execution.
        We just create the project and kick it off.
        """
        from app.models.project import Project
        from app.core.text import slugify

        project_name = f"{business.slug}-{cycle.cycle_date}-eng"
        project_slug = slugify(project_name)

        # Check if project already exists (idempotency)
        existing = db.scalar(
            select(Project).where(Project.slug == project_slug)
        )
        if existing is not None:
            return {
                "status": "already_exists",
                "project_id": str(existing.id),
                "summary": f"Project {project_slug} already exists",
            }

        workspace_path = str(
            Path(
                self.settings.orchestrator_workspaces_root
                / f"business-{business.slug}"
                / "engineering"
            ).resolve()
        )
        Path(workspace_path).mkdir(parents=True, exist_ok=True)

        project = Project(
            portfolio_id=business.portfolio_id,
            name=project_name,
            slug=project_slug,
            objective=task_description,
            repo_path=workspace_path,
            default_branch="main",
            status="active",
            metadata_json={
                "business_id": str(business.id),
                "cycle_id": str(cycle.id),
                "agent_type": "engineer",
            },
        )
        db.add(project)
        db.flush()
        db.commit()
        db.refresh(project)

        # Spawn a worker session for this project
        launch_plan = self.session_supervisor.plan_session(
            role=SessionRole.WORKER,
            runtime_preference="claude_code",
            allow_simulation_fallback=True,
        )

        worker_session = SessionModel(
            portfolio_id=business.portfolio_id,
            project_id=project.id,
            role=SessionRole.WORKER,
            status=launch_plan.initial_status,
            transport=launch_plan.transport,
            adapter_kind=launch_plan.adapter_kind,
            command=launch_plan.command,
            workspace_path=workspace_path,
            blocked_reason=launch_plan.blocked_reason,
            metadata_json={
                "session_kind": "business_engineer",
                "business_id": str(business.id),
                "cycle_id": str(cycle.id),
                "simulation_mode": launch_plan.simulation_mode,
                "runtime": launch_plan.runtime.model_dump(mode="json"),
            },
            runtime_metadata_json={},
        )
        db.add(worker_session)
        db.flush()

        # Link worker to project
        project.worker_session_id = worker_session.id
        db.commit()
        db.refresh(worker_session)

        if worker_session.status == SessionStatus.PENDING:
            try:
                self.session_supervisor.start_session(
                    worker_session.id,
                    initial_message=task_description,
                )
            except Exception as exc:
                return {
                    "status": "start_failed",
                    "project_id": str(project.id),
                    "error": str(exc),
                }

        return {
            "status": "delegated",
            "project_id": str(project.id),
            "worker_session_id": str(worker_session.id),
            "summary": f"Engineering project created and worker started",
        }

    def _delegate_to_reasoning_agent(
        self,
        business: Business,
        task_description: str,
        *,
        agent_type: str,
    ) -> dict[str, Any]:
        """Delegate a reasoning-only task (research/marketing) via Claude API.

        Uses ClaudeAPIAdapter instead of spawning a full Claude Code process.
        Cheaper and faster for tasks that don't need terminal access.
        """
        prompt_file = self._prompts_dir / f"business_{agent_type}.md"
        if not prompt_file.exists():
            return {
                "status": "error",
                "error": f"Prompt file not found: {prompt_file}",
            }

        system_prompt = prompt_file.read_text(encoding="utf-8").strip()
        user_message = (
            f"Business: {business.name}\n"
            f"Type: {business.business_type}\n"
            f"Description: {business.description}\n"
            f"\n"
            f"Task: {task_description}\n"
            f"\n"
            f"Execute this task and emit your result as a structured [[EVENT]] block."
        )

        try:
            adapter = ClaudeAPIAdapter()
            response = adapter.call(system_prompt, user_message)

            # Extract the complete event
            complete_event = None
            for event in response.events:
                if event.get("type") == "complete":
                    complete_event = event
                    break

            return {
                "status": "completed",
                "agent": agent_type,
                "summary": complete_event.get("summary", "Task completed") if complete_event else "No structured output",
                "result": complete_event if complete_event else {"raw": response.content[:2000]},
                "tokens": {
                    "input": response.input_tokens,
                    "output": response.output_tokens,
                },
            }
        except Exception as exc:
            logger.exception("reasoning agent %s failed for business %s", agent_type, business.id)
            return {
                "status": "error",
                "agent": agent_type,
                "error": str(exc),
            }

    # ── auto-deploy ─────────────────────────────────────────────────

    def _auto_deploy(
        self, business: Business, engineer_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Push engineer's workspace to GitHub and deploy to Vercel."""
        workspace_path = str(
            Path(
                self.settings.orchestrator_workspaces_root
                / f"business-{business.slug}"
                / "engineering"
            ).resolve()
        )

        if not Path(workspace_path).exists():
            return {"status": "skipped", "reason": "workspace not found"}

        repo_name = f"business-{business.slug}"
        deployer = DeployService()
        result = deployer.deploy_workspace(workspace_path, repo_name)

        # Update business infra_state with deploy info
        infra = dict(business.infra_state)
        if result.get("git", {}).get("status") == "pushed":
            infra["github"] = {
                "repo": result["git"].get("full_name", ""),
                "url": result["git"].get("html_url", ""),
            }
        if result.get("vercel", {}).get("status") == "deployed":
            infra["vercel"] = {
                "url": result["vercel"].get("url", ""),
                "project_id": result["vercel"].get("project_id", ""),
            }
            # Update domain if we got a Vercel URL
            if not business.domain and result["vercel"].get("url"):
                business.domain = result["vercel"]["url"]
        business.infra_state = infra

        logger.info(
            "auto-deploy for business %s: git=%s vercel=%s",
            business.name,
            result.get("git", {}).get("status"),
            result.get("vercel", {}).get("status"),
        )
        return result

    # ── digest ──────────────────────────────────────────────────────

    def _send_digest(
        self,
        db: Session,
        services: _ScopedServices,
        business: Business,
        cycle: BusinessCycle,
    ) -> None:
        """Send daily digest email after cycle completes."""
        digest_email = business.metadata_json.get("digest_email")
        if not digest_email:
            logger.info(
                "no digest_email configured for business %s — skipping digest",
                business.name,
            )
            return

        try:
            services.digest_service.send_daily_digest(business.id, digest_email)
        except Exception:
            logger.exception(
                "failed to send digest for business %s", business.id
            )

    # ── service construction ────────────────────────────────────────

    def _services(self, db: Session) -> _ScopedServices:
        event_service = EventService(
            db=db,
            redis_bus=self.redis_bus,
            event_broker=self.event_broker,
        )
        cycle_service = BusinessCycleService(
            db=db,
            settings=self.settings,
            event_service=event_service,
        )
        digest_service = DigestService(
            db=db,
            event_service=event_service,
        )
        return _ScopedServices(
            event_service=event_service,
            cycle_service=cycle_service,
            digest_service=digest_service,
        )
