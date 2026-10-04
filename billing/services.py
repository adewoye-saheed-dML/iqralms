"""Services for platform subscription management and webhook processing (Phase 9a)."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rest_framework.exceptions import ValidationError as DRFValidationError

from payments.paystack import PaystackClient
from .constants import SUBSCRIPTION_TIERS, get_tier_plan_code
from .models import (
    BillingWebhookEvent,
    PlatformSubscription,
    SubscriptionStatus,
    SubscriptionTier,
)

logger = logging.getLogger(__name__)


def get_student_cap(organization) -> Optional[int]:
    """Return the student cap for the organization's subscription tier, or None if unlimited."""
    subscription = getattr(organization, "platform_subscription", None)
    if not subscription:
        # Default to starter tier cap if none exists yet
        return SUBSCRIPTION_TIERS["starter"]["student_cap"]
    tier = subscription.tier
    tier_info = SUBSCRIPTION_TIERS.get(tier, {})
    return tier_info.get("student_cap")


def get_active_student_count(organization) -> int:
    """Return the count of active students in the organization."""
    from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
    return OrganizationMembership.objects.filter(
        organization=organization,
        role=OrganizationRole.STUDENT,
        status=MembershipStatus.ACTIVE,
    ).count()


def can_add_student(organization, additional_count: int = 1) -> bool:
    """Check if an organization can add additional_count student(s) under its tier cap.
    
    Returns True if unlimited (institutional tier) or if within student cap limit.
    """
    cap = get_student_cap(organization)
    if cap is None:
        return True
    current = get_active_student_count(organization)
    return (current + additional_count) <= cap


def get_student_cap_error_message(organization) -> str:
    """Generate the specific limit message required by the Phase 9 spec."""
    subscription = getattr(organization, "platform_subscription", None)
    tier = subscription.tier if subscription else "starter"
    tier_name = SUBSCRIPTION_TIERS.get(tier, {}).get("name", "Starter")
    return f"This academy has reached its student limit for the {tier_name} plan."


def check_can_add_student(organization, additional_count: int = 1) -> None:
    """Raise ValidationError if the organization has reached its student limit."""
    from django.core.exceptions import ValidationError
    if not can_add_student(organization, additional_count):
        raise ValidationError(
            {"organization": get_student_cap_error_message(organization)},
            code="student_cap_reached",
        )


import uuid
from organizations.models import OrganizationRole


def initialize_platform_subscription(
    organization,
    user,
    tier: str = "starter",
    plan_code: Optional[str] = None,
    callback_url: Optional[str] = None,
    client: Optional[PaystackClient] = None,
) -> Tuple[PlatformSubscription, Dict[str, Any]]:
    """Initialize first platform subscription payment via Paystack transaction/initialize with plan."""
    if tier == SubscriptionTier.INSTITUTIONAL or tier == "institutional":
        raise DRFValidationError("Institutional tier is sales-assisted and cannot be subscribed self-serve.")

    if tier not in SUBSCRIPTION_TIERS:
        raise DRFValidationError(f"Invalid tier: {tier}. Must be one of {list(SUBSCRIPTION_TIERS.keys())}.")

    tier_info = SUBSCRIPTION_TIERS[tier]
    price_ngn = tier_info.get("price_ngn")
    if not price_ngn:
        raise DRFValidationError("Selected tier has no payable rate configured.")

    amount_kobo = int(price_ngn * 100)
    resolved_plan_code = plan_code or get_tier_plan_code(tier)
    if not resolved_plan_code:
        resolved_plan_code = getattr(settings, "PAYSTACK_PLAN_CODE", "PLN_monthly_sub")

    client = client or PaystackClient()
    reference = f"sub_{uuid.uuid4().hex[:16]}"

    metadata = {
        "organization_id": organization.pk,
        "tier": tier,
        "type": "platform_subscription",
    }

    init_data = client.initialize_transaction(
        email=user.email,
        amount_kobo=amount_kobo,
        reference=reference,
        plan_code=resolved_plan_code,
        callback_url=callback_url,
        metadata=metadata,
    )

    with transaction.atomic():
        subscription = PlatformSubscription.objects.filter(organization=organization).first()
        if subscription:
            subscription.paystack_initialization_reference = reference
            subscription.paystack_plan_code = resolved_plan_code
            if subscription.status == SubscriptionStatus.PENDING:
                subscription.tier = tier
            else:
                subscription.pending_tier = tier
            subscription.save()
        else:
            subscription = PlatformSubscription.objects.create(
                organization=organization,
                tier=tier,
                paystack_plan_code=resolved_plan_code,
                paystack_initialization_reference=reference,
                status=SubscriptionStatus.PENDING,
            )

    return subscription, init_data


def subscribe_organization(
    organization,
    user,
    tier: str = "starter",
    plan_code: Optional[str] = None,
    client: Optional[PaystackClient] = None,
) -> PlatformSubscription:
    """Subscribe an academy to a self-serve IqraLMS platform plan (starter, growth, professional)."""
    sub, _ = initialize_platform_subscription(
        organization=organization,
        user=user,
        tier=tier,
        plan_code=plan_code,
        client=client,
    )
    return sub


def create_institutional_subscription(
    organization,
    user=None,
) -> PlatformSubscription:
    """Manually provision an institutional platform subscription for an academy (sales-assisted)."""
    with transaction.atomic():
        subscription, _ = PlatformSubscription.objects.update_or_create(
            organization=organization,
            defaults={
                "tier": SubscriptionTier.INSTITUTIONAL,
                "paystack_plan_code": None,
                "paystack_customer_code": getattr(user, "email", "") if user else "",
                "status": SubscriptionStatus.ACTIVE,
            },
        )
        if not organization.is_active:
            organization.is_active = True
            organization.save(update_fields=["is_active", "updated_at"])

    return subscription



def _find_subscription_for_event(data: Dict[str, Any]) -> Optional[PlatformSubscription]:
    """Resolve a PlatformSubscription from a webhook event data object."""
    ref = data.get("reference")
    if ref:
        sub = PlatformSubscription.objects.filter(paystack_initialization_reference=ref).first()
        if sub:
            return sub

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

    org_id = data.get("metadata", {}).get("organization_id")
    if org_id:
        sub = PlatformSubscription.objects.filter(organization_id=org_id).first()
        if sub:
            return sub

    customer_email = data.get("customer", {}).get("email")
    if customer_email:
        sub = PlatformSubscription.objects.filter(
            organization__memberships__user__email=customer_email,
            organization__memberships__role=OrganizationRole.OWNER,
        ).first()
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
            if subscription.pending_tier:
                subscription.tier = subscription.pending_tier
                subscription.pending_tier = None
            if subscription_code:
                subscription.paystack_subscription_code = subscription_code
            if email_token:
                subscription.paystack_email_token = email_token
            if customer_code:
                subscription.paystack_customer_code = customer_code
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
