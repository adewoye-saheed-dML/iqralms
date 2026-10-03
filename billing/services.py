"""Services for platform subscription management and webhook processing (Phase 9a)."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from payments.paystack import PaystackClient
from .models import BillingWebhookEvent, PlatformSubscription, SubscriptionStatus

logger = logging.getLogger(__name__)


def subscribe_organization(
    organization,
    user,
    plan_code: Optional[str] = None,
    client: Optional[PaystackClient] = None,
) -> PlatformSubscription:
    """Subscribe an academy to the IqraLMS platform plan."""
    client = client or PaystackClient()
    plan_code = plan_code or getattr(settings, "PAYSTACK_PLAN_CODE", "PLN_monthly_sub")

    subscription = PlatformSubscription.objects.filter(organization=organization).first()
    customer_code = subscription.paystack_customer_code if subscription else None

    if not customer_code:
        customer_res = client.create_customer(
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
        )
        customer_code = customer_res.get("customer_code") or customer_res.get("id")

    sub_res = client.create_subscription(
        customer_code_or_email=customer_code,
        plan_code=plan_code,
    )
    subscription_code = sub_res.get("subscription_code", "")
    email_token = sub_res.get("email_token", "")

    with transaction.atomic():
        if subscription:
            subscription.paystack_plan_code = plan_code
            subscription.paystack_customer_code = customer_code
            subscription.paystack_subscription_code = subscription_code
            subscription.paystack_email_token = email_token
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.save()
        else:
            subscription = PlatformSubscription.objects.create(
                organization=organization,
                paystack_plan_code=plan_code,
                paystack_customer_code=customer_code,
                paystack_subscription_code=subscription_code,
                paystack_email_token=email_token,
                status=SubscriptionStatus.ACTIVE,
            )

        if not organization.is_active:
            organization.is_active = True
            organization.save(update_fields=["is_active", "updated_at"])

    return subscription


def _find_subscription_for_event(data: Dict[str, Any]) -> Optional[PlatformSubscription]:
    """Resolve a PlatformSubscription from a webhook event data object."""
    subscription_code = data.get("subscription_code") or data.get("code")
    if subscription_code:
        sub = PlatformSubscription.objects.filter(paystack_subscription_code=subscription_code).first()
        if sub:
            return sub

    customer_code = data.get("customer", {}).get("customer_code")
    if customer_code:
        sub = PlatformSubscription.objects.filter(paystack_customer_code=customer_code).first()
        if sub:
            return sub

    customer_email = data.get("customer", {}).get("email")
    if customer_email:
        sub = PlatformSubscription.objects.filter(organization__memberships__user__email=customer_email).first()
        if sub:
            return sub

    return None


@transaction.atomic
def process_billing_webhook(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """Process a Paystack platform subscription webhook event.

    Ensures at-most-once processing using BillingWebhookEvent.
    Returns (success, message).
    """
    event_type = payload.get("event")
    data = payload.get("data", {})

    # Extract or synthesize a deterministic event id
    event_id = str(payload.get("id") or data.get("id") or f"{event_type}_{data.get('reference') or data.get('subscription_code')}")
    if BillingWebhookEvent.objects.filter(event_id=event_id).exists():
        logger.info("Billing webhook event %s already processed; ignoring.", event_id)
        return True, "already_processed"

    subscription = _find_subscription_for_event(data)

    if event_type == "subscription.create":
        subscription_code = data.get("subscription_code", "")
        email_token = data.get("email_token", "")
        customer_code = data.get("customer", {}).get("customer_code", "")

        if subscription:
            subscription.status = SubscriptionStatus.ACTIVE
            if subscription_code:
                subscription.paystack_subscription_code = subscription_code
            if email_token:
                subscription.paystack_email_token = email_token
            subscription.save()
            if not subscription.organization.is_active:
                subscription.organization.is_active = True
                subscription.organization.save(update_fields=["is_active", "updated_at"])

    elif event_type == "invoice.create":
        # Informational: Paystack is about to attempt next charge
        logger.info("Invoice create event received for subscription %s", subscription)

    elif event_type == "invoice.update":
        # Charge succeeded; refresh current_period_end
        if subscription:
            next_payment = data.get("subscription", {}).get("next_payment_date") or data.get("period_end")
            if next_payment:
                dt = parse_datetime(next_payment)
                if dt:
                    subscription.current_period_end = dt
            if subscription.status == SubscriptionStatus.PAST_DUE:
                subscription.status = SubscriptionStatus.ACTIVE
            subscription.save()

    elif event_type == "invoice.payment_failed":
        # Start dunning: status = past_due.
        # DO NOT flip organization.is_active to False (Decision D-012)
        if subscription:
            subscription.status = SubscriptionStatus.PAST_DUE
            subscription.save(update_fields=["status", "updated_at"])
            logger.warning("Subscription %s past due; dunning alert active.", subscription.pk)

    elif event_type == "subscription.not_renew":
        if subscription:
            subscription.status = SubscriptionStatus.NOT_RENEWING
            subscription.save(update_fields=["status", "updated_at"])

    elif event_type == "subscription.disable":
        # Hard cutoff: status = disabled, and Organization.is_active = False
        if subscription:
            subscription.status = SubscriptionStatus.DISABLED
            subscription.save(update_fields=["status", "updated_at"])
            org = subscription.organization
            org.is_active = False
            org.save(update_fields=["is_active", "updated_at"])
            logger.warning("Subscription %s disabled; Organization %s deactivated.", subscription.pk, org.slug)

    BillingWebhookEvent.objects.create(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
    )
    return True, "processed"
