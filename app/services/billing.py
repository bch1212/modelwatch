"""Stripe billing — checkout sessions, webhook handling, plan enforcement."""

import logging
from typing import Optional
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.schemas import Workspace, Plan

logger = logging.getLogger(__name__)


def _init_stripe():
    settings = get_settings()
    stripe.api_key = settings.stripe_secret_key


def get_price_for_plan(plan: Plan) -> Optional[str]:
    settings = get_settings()
    return {
        Plan.pro: settings.stripe_price_pro,
        Plan.team: settings.stripe_price_team,
        Plan.enterprise: settings.stripe_price_enterprise,
    }.get(plan)


async def create_checkout_session(
    workspace: Workspace,
    plan: Plan,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout session and return the URL."""
    _init_stripe()
    price_id = get_price_for_plan(plan)
    if not price_id:
        raise ValueError(f"No Stripe price configured for plan: {plan}")

    # Create or reuse Stripe customer
    if not workspace.stripe_customer_id:
        customer = stripe.Customer.create(
            metadata={"workspace_id": str(workspace.id)},
        )
        workspace.stripe_customer_id = customer.id

    session = stripe.checkout.Session.create(
        customer=workspace.stripe_customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"workspace_id": str(workspace.id), "plan": plan.value},
    )
    return session.url


async def handle_checkout_completed(
    db: AsyncSession,
    session_data: dict,
):
    """Process checkout.session.completed webhook."""
    workspace_id = session_data.get("metadata", {}).get("workspace_id")
    plan_str = session_data.get("metadata", {}).get("plan")
    subscription_id = session_data.get("subscription")

    if not workspace_id or not plan_str:
        logger.warning("Checkout webhook missing metadata")
        return

    result = await db.execute(
        select(Workspace).where(Workspace.id == UUID(workspace_id))
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        logger.warning(f"Workspace {workspace_id} not found for checkout webhook")
        return

    workspace.plan = Plan(plan_str)
    workspace.stripe_subscription_id = subscription_id
    workspace.stripe_customer_id = session_data.get("customer")


async def handle_subscription_deleted(
    db: AsyncSession,
    subscription_data: dict,
):
    """Process customer.subscription.deleted → downgrade to free."""
    sub_id = subscription_data.get("id")
    result = await db.execute(
        select(Workspace).where(Workspace.stripe_subscription_id == sub_id)
    )
    workspace = result.scalar_one_or_none()
    if workspace:
        workspace.plan = Plan.free
        workspace.stripe_subscription_id = None
        logger.info(f"Workspace {workspace.id} downgraded to free")


async def handle_invoice_failed(
    db: AsyncSession,
    invoice_data: dict,
):
    """Process invoice.payment_failed — log for now."""
    sub_id = invoice_data.get("subscription")
    logger.warning(f"Payment failed for subscription {sub_id}")


# ── Plan limit checks ───────────────────────────────────────────────

def get_limits(plan: Plan) -> dict:
    settings = get_settings()
    return {
        Plan.free: {
            "specs": settings.free_specs,
            "runs_per_month": settings.free_runs_per_month,
            "endpoints": settings.free_endpoints,
        },
        Plan.pro: {
            "specs": settings.pro_specs,
            "runs_per_month": settings.pro_runs_per_month,
            "endpoints": settings.pro_endpoints,
        },
        Plan.team: {
            "specs": settings.team_specs,
            "runs_per_month": settings.team_runs_per_month,
            "endpoints": settings.team_endpoints,
        },
        Plan.enterprise: {
            "specs": settings.enterprise_specs,
            "runs_per_month": settings.enterprise_runs_per_month,
            "endpoints": settings.enterprise_endpoints,
        },
    }[plan]


def check_limit(current: int, limit: int, resource: str) -> None:
    """Raise ValueError if limit exceeded."""
    if current >= limit:
        raise ValueError(
            f"{resource} limit reached ({limit}). Upgrade your plan."
        )
