"""Daily digest email service — compiles business state and sends summary.

Uses the Resend API to deliver a formatted email to the business owner
after each daily cycle completes.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import BusinessCycleStatus, EventCategory, EventLevel
from app.models.business import Business
from app.models.business_cycle import BusinessCycle
from app.schemas.event import EventCreate, EventSourceRef
from app.services.event_service import EventService

logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com"


class DigestService:
    def __init__(
        self,
        db: Session,
        event_service: EventService,
        *,
        resend_api_key: str | None = None,
        from_email: str | None = None,
        dashboard_base_url: str = "http://localhost:3000",
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.resend_api_key = resend_api_key or os.environ.get("RESEND_API_KEY", "")
        self.from_email = from_email or os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
        self.dashboard_base_url = dashboard_base_url

    def send_daily_digest(
        self,
        business_id: UUID,
        to_email: str,
    ) -> bool:
        """Compile and send a daily digest for a business. Returns True on success."""
        business = self.db.get(Business, business_id)
        if business is None:
            logger.warning("Cannot send digest: business %s not found", business_id)
            return False

        # Get today's completed cycle
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        cycle = self.db.scalar(
            select(BusinessCycle).where(
                BusinessCycle.business_id == business_id,
                BusinessCycle.cycle_date == today,
                BusinessCycle.status == BusinessCycleStatus.COMPLETED,
            )
        )

        digest = self._compile_digest(business, cycle)
        html = self._render_html(digest)
        subject = f"[{business.name}] Daily Digest — {today}"

        success = self._send_email(to_email, subject, html)

        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="business.digest_sent" if success else "business.digest_failed",
                level=EventLevel.INFO if success else EventLevel.WARN,
                source=EventSourceRef(kind="service", id="digest-service"),
                payload={
                    "business_id": str(business_id),
                    "to_email": to_email,
                    "cycle_date": today,
                },
            )
        )
        return success

    def _compile_digest(
        self, business: Business, cycle: BusinessCycle | None
    ) -> dict[str, Any]:
        metrics = dict(business.metrics_snapshot)
        metrics["total_revenue"] = str(business.total_revenue_usd)
        metrics["total_cost"] = str(business.total_cost_usd)
        metrics["budget_remaining"] = str(business.budget_monthly_usd - business.total_cost_usd)

        actions_today: list[str] = []
        plan_tomorrow: list[str] = []
        needs_approval: list[str] = []

        if cycle:
            # Extract from agent results
            for agent, result in cycle.agent_results.items():
                if isinstance(result, dict):
                    summary = result.get("summary", f"{agent} completed work")
                    actions_today.append(f"[{agent}] {summary}")

            # Extract from CEO plan
            ceo_plan = cycle.ceo_plan
            if isinstance(ceo_plan, dict):
                priorities = ceo_plan.get("priorities", [])
                for p in priorities:
                    if isinstance(p, dict):
                        plan_tomorrow.append(f"[{p.get('agent', '?')}] {p.get('task', '?')}")

                attention = ceo_plan.get("human_attention")
                if attention:
                    needs_approval.append(str(attention))

        return {
            "business_name": business.name,
            "business_type": business.business_type,
            "status": business.status,
            "metrics": metrics,
            "actions_today": actions_today,
            "plan_tomorrow": plan_tomorrow,
            "needs_approval": needs_approval,
            "cycle_date": cycle.cycle_date if cycle else datetime.now(UTC).strftime("%Y-%m-%d"),
            "dashboard_url": f"{self.dashboard_base_url}/businesses/{business.id}",
        }

    def _render_html(self, digest: dict[str, Any]) -> str:
        metrics = digest.get("metrics", {})
        metrics_html = "".join(f"<li><b>{k}:</b> {v}</li>" for k, v in metrics.items())
        actions_html = "".join(f"<li>{a}</li>" for a in digest.get("actions_today", []))
        plan_html = "".join(f"<li>{p}</li>" for p in digest.get("plan_tomorrow", []))

        approval_section = ""
        approvals = digest.get("needs_approval", [])
        if approvals:
            items = "".join(f"<li style='color: #d97706;'>⚠ {a}</li>" for a in approvals)
            approval_section = f"<h3 style='color: #d97706;'>Needs Your Attention</h3><ul>{items}</ul>"

        dashboard_url = digest.get("dashboard_url", "")
        dashboard_link = f'<p><a href="{dashboard_url}" style="color: #2563eb;">View Dashboard →</a></p>' if dashboard_url else ""

        return f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                    max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="border-bottom: 2px solid #e5e7eb; padding-bottom: 10px;">
                {digest['business_name']} — Daily Digest
            </h2>
            <p style="color: #6b7280;">
                {digest.get('cycle_date', '')} | Status: {digest.get('status', 'unknown')}
            </p>

            <h3>Key Metrics</h3>
            <ul>{metrics_html or '<li>No metrics yet</li>'}</ul>

            <h3>Today's Activity</h3>
            <ul>{actions_html or '<li>No activity recorded</li>'}</ul>

            <h3>Tomorrow's Plan</h3>
            <ul>{plan_html or '<li>No plan set yet</li>'}</ul>

            {approval_section}
            {dashboard_link}

            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;">
            <p style="color: #9ca3af; font-size: 12px;">
                Sent by Poulpe Autonomous Business Agent
            </p>
        </div>
        """

    def _send_email(self, to: str, subject: str, html: str) -> bool:
        if not self.resend_api_key:
            logger.warning("RESEND_API_KEY not set — skipping email to %s", to)
            return False

        try:
            with httpx.Client(
                base_url=RESEND_API,
                headers={"Authorization": f"Bearer {self.resend_api_key}"},
                timeout=15.0,
            ) as client:
                resp = client.post(
                    "/emails",
                    json={
                        "from": self.from_email,
                        "to": [to],
                        "subject": subject,
                        "html": html,
                    },
                )
                resp.raise_for_status()
                logger.info("Digest email sent to %s (id=%s)", to, resp.json().get("id"))
                return True
        except Exception:
            logger.exception("Failed to send digest email to %s", to)
            return False
