"""Inbound Service — handles incoming emails, support requests, and customer queries.

Receives inbound emails (via Resend webhook or similar) and routes them
to a support agent that can respond with scoped authority.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.claude_api_adapter import ClaudeAPIAdapter
from app.core.enums import EventCategory, EventLevel
from app.models.business import Business
from app.schemas.event import EventCreate, EventSourceRef
from app.services.event_service import EventService

logger = logging.getLogger(__name__)

SUPPORT_SYSTEM_PROMPT = """# Business Support Agent

You are the customer support agent for {business_name}. You handle inbound emails from customers and prospects.

## Your Authority (Scoped)
- You CAN: answer product questions, troubleshoot common issues, provide documentation links, acknowledge bugs
- You CAN: offer refunds under $50 without approval
- You CANNOT: modify pricing, access payment systems, make promises about unreleased features
- You CANNOT: share internal business metrics or agent architecture details
- You MUST: escalate anything involving security, legal, or payments over $50 to human attention

## Response Style
- Professional but friendly
- Concise — under 150 words for simple queries
- Include relevant links when available
- If you don't know, say so and escalate

## Business Context
Name: {business_name}
Type: {business_type}
Description: {business_description}
Domain: {domain}

## Output Format
Respond with a JSON block:
```json
{{
  "action": "reply" | "escalate",
  "reply_to": "email@example.com",
  "subject": "Re: ...",
  "body": "Your response here",
  "escalation_reason": "null or reason if escalating",
  "tags": ["support", "billing", "bug", etc]
}}
```
"""


class InboundService:
    """Handles inbound communications for businesses."""

    def __init__(
        self,
        db: Session,
        event_service: EventService,
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.resend_api_key = os.environ.get("RESEND_API_KEY", "")
        self.resend_from = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")

    def handle_inbound_email(
        self,
        business_id: UUID,
        from_email: str,
        subject: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Process an inbound email and generate/send a response."""
        business = self.db.get(Business, business_id)
        if business is None:
            return {"status": "error", "reason": "Business not found"}

        # Build support agent prompt with business context
        system_prompt = SUPPORT_SYSTEM_PROMPT.format(
            business_name=business.name,
            business_type=business.business_type,
            business_description=business.description or "(no description)",
            domain=business.domain or "(no domain)",
        )

        user_message = (
            f"From: {from_email}\n"
            f"Subject: {subject}\n"
            f"\n"
            f"{body}"
        )

        # Call Claude API for support agent reasoning
        try:
            adapter = ClaudeAPIAdapter(model="claude-haiku-4-5-20251001")  # Haiku for speed + cost
            response = adapter.call(system_prompt, user_message, max_tokens=1024)

            # Try to parse JSON response
            import json
            import re

            json_match = re.search(r'\{[^{}]*"action"[^{}]*\}', response.content, re.DOTALL)
            if json_match:
                decision = json.loads(json_match.group())
            else:
                decision = {
                    "action": "reply",
                    "reply_to": from_email,
                    "subject": f"Re: {subject}",
                    "body": response.content[:1000],
                    "tags": ["auto-generated"],
                }

        except Exception as exc:
            logger.exception("support agent failed for business %s", business.id)
            decision = {
                "action": "escalate",
                "escalation_reason": f"Agent error: {exc}",
                "reply_to": from_email,
                "subject": f"Re: {subject}",
                "body": "",
                "tags": ["error"],
            }

        # Execute the decision
        result: dict[str, Any] = {"decision": decision}

        if decision.get("action") == "reply" and decision.get("body"):
            send_result = self._send_reply(
                to=decision.get("reply_to", from_email),
                subject=decision.get("subject", f"Re: {subject}"),
                body=decision["body"],
                business_name=business.name,
            )
            result["send_result"] = send_result
        elif decision.get("action") == "escalate":
            result["escalated"] = True
            result["escalation_reason"] = decision.get("escalation_reason", "Unknown")

        # Record event
        self.event_service.record_event(
            EventCreate(
                category=EventCategory.SYSTEM,
                event_type="business.inbound_email_handled",
                level=EventLevel.INFO,
                source=EventSourceRef(kind="service", id="inbound-service"),
                payload={
                    "business_id": str(business.id),
                    "from": from_email,
                    "subject": subject,
                    "action": decision.get("action", "unknown"),
                    "tags": decision.get("tags", []),
                },
            )
        )

        # Update business metrics
        snapshot = dict(business.metrics_snapshot)
        snapshot["support_emails_handled"] = snapshot.get("support_emails_handled", 0) + 1
        if decision.get("action") == "escalate":
            snapshot["support_escalations"] = snapshot.get("support_escalations", 0) + 1
        business.metrics_snapshot = snapshot
        self.db.commit()

        return result

    def _send_reply(
        self,
        to: str,
        subject: str,
        body: str,
        business_name: str,
    ) -> dict[str, Any]:
        """Send a support reply email via Resend."""
        if not self.resend_api_key:
            return {"status": "skipped", "reason": "RESEND_API_KEY not set"}

        import httpx

        html = f"""
        <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
            <p>{body.replace(chr(10), '<br>')}</p>
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;">
            <p style="color: #9ca3af; font-size: 12px;">
                {business_name} Support
            </p>
        </div>
        """

        try:
            with httpx.Client(
                base_url="https://api.resend.com",
                headers={"Authorization": f"Bearer {self.resend_api_key}"},
                timeout=15.0,
            ) as client:
                resp = client.post("/emails", json={
                    "from": self.resend_from,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                })
                resp.raise_for_status()
                return {"status": "sent", "email_id": resp.json().get("id")}
        except Exception as exc:
            logger.exception("failed to send support reply")
            return {"status": "error", "reason": str(exc)}
