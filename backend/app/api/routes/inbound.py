"""Inbound email webhook — receives emails and routes to support agent."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_event_service
from app.services.event_service import EventService
from app.services.inbound_service import InboundService

router = APIRouter(prefix="/inbound", tags=["inbound"])


class InboundEmailPayload(BaseModel):
    business_id: str
    from_email: str
    subject: str
    body: str
    headers: dict[str, str] | None = None


@router.post("/email")
def handle_inbound_email(
    payload: InboundEmailPayload,
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
) -> dict[str, Any]:
    """Receive an inbound email and route to support agent."""
    service = InboundService(db=db, event_service=event_service)
    return service.handle_inbound_email(
        business_id=UUID(payload.business_id),
        from_email=payload.from_email,
        subject=payload.subject,
        body=payload.body,
        headers=payload.headers,
    )


class ResendInboundWebhook(BaseModel):
    """Resend inbound email webhook payload."""
    type: str = ""
    data: dict[str, Any] = {}


@router.post("/resend-webhook")
def handle_resend_inbound(
    payload: ResendInboundWebhook,
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
) -> dict[str, str]:
    """Handle Resend inbound email webhook.

    Set up in Resend dashboard: forward inbound emails to this endpoint.
    Map the receiving email address to a business_id in business.infra_state.
    """
    if payload.type != "email.received":
        return {"received": "true", "note": "ignored non-email event"}

    data = payload.data
    from_email = data.get("from", "")
    to_email = data.get("to", [""])[0] if isinstance(data.get("to"), list) else data.get("to", "")
    subject = data.get("subject", "")
    body = data.get("text", data.get("html", ""))

    if not from_email or not body:
        return {"received": "true", "note": "missing from or body"}

    # Look up business by receiving email address
    from app.models.business import Business
    from sqlalchemy import select

    businesses = db.scalars(select(Business)).all()
    target_business = None
    for biz in businesses:
        infra = biz.infra_state or {}
        biz_email = infra.get("email", {}).get("inbound_address", "")
        if biz_email and biz_email.lower() == to_email.lower():
            target_business = biz
            break

    if target_business is None:
        return {"received": "true", "note": f"no business mapped to {to_email}"}

    service = InboundService(db=db, event_service=event_service)
    service.handle_inbound_email(
        business_id=target_business.id,
        from_email=from_email,
        subject=subject,
        body=body,
    )
    return {"received": "true"}
