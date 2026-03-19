"""Billing API routes — user provisioning via Clerk webhook + subscription status."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.billing import Subscription, SubscriptionStatus, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


class UserOut(BaseModel):
    id: str
    clerk_user_id: str
    email: str
    stripe_customer_id: str | None

    model_config = {"from_attributes": True}


class SubscriptionOut(BaseModel):
    id: str
    stripe_subscription_id: str
    stripe_price_id: str
    status: SubscriptionStatus

    model_config = {"from_attributes": True}


@router.get("/me", response_model=UserOut)
def get_current_user(
    x_clerk_user_id: str = Header(..., alias="x-clerk-user-id"),
    db: Session = Depends(get_db),
):
    """Return the billing user record for the authenticated Clerk user."""
    user = db.query(User).filter(User.clerk_user_id == x_clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/me/subscription", response_model=SubscriptionOut | None)
def get_active_subscription(
    x_clerk_user_id: str = Header(..., alias="x-clerk-user-id"),
    db: Session = Depends(get_db),
):
    """Return the user's active/trialing subscription if one exists."""
    user = db.query(User).filter(User.clerk_user_id == x_clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    sub = (
        db.query(Subscription)
        .filter(
            Subscription.user_id == user.id,
            Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.trialing]),
        )
        .first()
    )
    return sub


@router.post("/webhook/clerk", include_in_schema=False)
async def clerk_webhook(request: Request, db: Session = Depends(get_db)):
    """Provision or update a User row from a Clerk user.created/updated webhook."""
    payload = await request.json()
    event_type: str = payload.get("type", "")
    data = payload.get("data", {})

    clerk_user_id: str = data.get("id", "")
    email_addresses: list[dict] = data.get("email_addresses", [])
    primary_email = next(
        (e["email_address"] for e in email_addresses if e.get("id") == data.get("primary_email_address_id")),
        email_addresses[0]["email_address"] if email_addresses else "",
    )

    if event_type in ("user.created", "user.updated"):
        existing = db.query(User).filter(User.clerk_user_id == clerk_user_id).first()
        if existing:
            existing.email = primary_email
        else:
            import uuid
            db.add(User(id=str(uuid.uuid4()), clerk_user_id=clerk_user_id, email=primary_email))
        db.commit()

    return {"ok": True}
