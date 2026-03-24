"""Stripe webhook handler — receives payment events and updates business metrics.

Listens for checkout.session.completed, invoice.paid, customer.subscription.deleted
and updates the Business model's revenue and metrics accordingly.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_event_service
from app.core.enums import EventCategory, EventLevel
from app.models.business import Business
from app.schemas.event import EventCreate, EventSourceRef
from app.services.event_service import EventService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")


def _verify_stripe_signature(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Verify Stripe webhook signature and parse event."""
    try:
        import stripe
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        return dict(event)
    except ImportError:
        # If stripe package not installed, parse without verification
        import json
        logger.warning("stripe package not installed — skipping signature verification")
        return json.loads(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Webhook verification failed: {exc}")


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
    event_service: EventService = Depends(get_event_service),
) -> dict[str, str]:
    """Handle Stripe webhook events for business revenue tracking."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if STRIPE_WEBHOOK_SECRET and sig:
        event = _verify_stripe_signature(payload, sig)
    else:
        import json
        event = json.loads(payload)

    event_type = event.get("type", "")
    data_obj = event.get("data", {}).get("object", {})

    # Extract business_id from metadata
    metadata = data_obj.get("metadata", {})
    business_id_str = metadata.get("business_id", "")

    if not business_id_str:
        # Try to find business by Stripe customer ID
        customer_id = data_obj.get("customer", "")
        if customer_id:
            business = db.scalar(
                select(Business).where(
                    Business.infra_state["stripe"]["customer_id"].as_string() == customer_id
                )
            )
            if business:
                business_id_str = str(business.id)

    if not business_id_str:
        return {"received": "true", "note": "no business_id in metadata"}

    try:
        business_id = UUID(business_id_str)
    except ValueError:
        return {"received": "true", "note": "invalid business_id"}

    business = db.get(Business, business_id)
    if business is None:
        return {"received": "true", "note": "business not found"}

    # Process event types
    if event_type == "checkout.session.completed":
        amount = Decimal(str(data_obj.get("amount_total", 0))) / 100
        business.total_revenue_usd += amount
        _update_metric(business, "last_payment_amount", str(amount))
        _update_metric(business, "total_customers", business.metrics_snapshot.get("total_customers", 0) + 1)
        logger.info("business %s: payment received $%s", business.name, amount)

    elif event_type == "invoice.paid":
        amount = Decimal(str(data_obj.get("amount_paid", 0))) / 100
        business.total_revenue_usd += amount
        _update_metric(business, "last_invoice_amount", str(amount))
        _update_metric(business, "invoices_paid", business.metrics_snapshot.get("invoices_paid", 0) + 1)
        logger.info("business %s: invoice paid $%s", business.name, amount)

    elif event_type == "customer.subscription.created":
        _update_metric(business, "active_subscriptions", business.metrics_snapshot.get("active_subscriptions", 0) + 1)
        plan = data_obj.get("items", {}).get("data", [{}])[0].get("price", {}).get("nickname", "unknown")
        _update_metric(business, "last_subscription_plan", plan)
        logger.info("business %s: new subscription (%s)", business.name, plan)

    elif event_type == "customer.subscription.deleted":
        active = business.metrics_snapshot.get("active_subscriptions", 1)
        _update_metric(business, "active_subscriptions", max(0, active - 1))
        _update_metric(business, "churned_subscriptions", business.metrics_snapshot.get("churned_subscriptions", 0) + 1)
        logger.info("business %s: subscription cancelled", business.name)

    elif event_type == "charge.refunded":
        amount = Decimal(str(data_obj.get("amount_refunded", 0))) / 100
        business.total_revenue_usd -= amount
        _update_metric(business, "total_refunds", str(
            Decimal(business.metrics_snapshot.get("total_refunds", "0")) + amount
        ))
        logger.info("business %s: refund $%s", business.name, amount)

    db.commit()

    event_service.record_event(
        EventCreate(
            category=EventCategory.SYSTEM,
            event_type=f"business.stripe.{event_type}",
            level=EventLevel.INFO,
            source=EventSourceRef(kind="webhook", id="stripe"),
            payload={
                "business_id": business_id_str,
                "stripe_event_type": event_type,
                "amount": str(data_obj.get("amount_total", data_obj.get("amount_paid", 0))),
            },
        )
    )

    return {"received": "true"}


def _update_metric(business: Business, key: str, value: Any) -> None:
    """Update a single metric in the business's metrics_snapshot."""
    snapshot = dict(business.metrics_snapshot)
    snapshot[key] = value
    business.metrics_snapshot = snapshot
