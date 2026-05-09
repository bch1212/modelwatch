"""Stripe billing routes — checkout + webhooks."""

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.middleware.auth import get_current_workspace
from app.models.database import get_db
from app.models.schemas import Workspace, Plan
from app.models.api_models import CheckoutRequest, CheckoutResponse
from app.services.billing import (
    create_checkout_session,
    handle_checkout_completed,
    handle_subscription_deleted,
    handle_invoice_failed,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    if body.plan == Plan.free:
        raise HTTPException(status_code=400, detail="Cannot checkout for free plan")

    try:
        url = await create_checkout_session(
            workspace=workspace,
            plan=body.plan,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
        return CheckoutResponse(checkout_url=url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Stripe webhook events."""
    settings = get_settings()
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig, settings.stripe_webhook_secret,
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        await handle_checkout_completed(db, data)
    elif event_type == "customer.subscription.deleted":
        await handle_subscription_deleted(db, data)
    elif event_type == "invoice.payment_failed":
        await handle_invoice_failed(db, data)
    else:
        logger.debug(f"Unhandled Stripe event: {event_type}")

    return {"status": "ok"}
