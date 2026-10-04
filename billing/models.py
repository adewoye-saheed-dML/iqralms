"""Platform subscription models (Phase 9a).

Handles academy owner -> IqraLMS monthly platform subscription.
Lifecycle:
- subscription.create -> status = active, Organization.is_active = True
- invoice.create -> informational
- invoice.update -> charge succeeded, refresh current_period_end
- invoice.payment_failed -> status = past_due (dunning, is_active stays True)
- subscription.not_renew -> status = not_renewing (access maintained until period end)
- subscription.disable -> status = disabled, Organization.is_active = False
"""

from django.db import models


class SubscriptionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past Due"
    NOT_RENEWING = "not_renewing", "Not Renewing"
    DISABLED = "disabled", "Disabled"


class SubscriptionTier(models.TextChoices):
    STARTER = "starter", "Starter"
    GROWTH = "growth", "Growth"
    PROFESSIONAL = "professional", "Professional"
    INSTITUTIONAL = "institutional", "Institutional"


class PlatformSubscription(models.Model):
    """Recurring monthly subscription paid by an Academy Owner to IqraLMS."""

    organization = models.OneToOneField(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="platform_subscription",
        help_text="The academy holding this platform subscription.",
    )
    tier = models.CharField(
        max_length=30,
        choices=SubscriptionTier.choices,
        default=SubscriptionTier.STARTER,
        help_text="Subscription tier: starter, growth, professional, institutional.",
    )
    pending_tier = models.CharField(
        max_length=30,
        null=True,
        blank=True,
        help_text="Pending tier upgrade/switch awaiting webhook confirmation.",
    )
    paystack_plan_code = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Paystack Plan code for IqraLMS platform access (null for institutional).",
    )
    paystack_initialization_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Paystack transaction reference for pending first-time checkout.",
    )
    paystack_customer_code = models.CharField(
        max_length=100,
        blank=True,
        help_text="Paystack Customer code for the academy owner.",
    )
    paystack_subscription_code = models.CharField(
        max_length=100,
        blank=True,
        help_text="Paystack Subscription code.",
    )
    paystack_email_token = models.CharField(
        max_length=100,
        blank=True,
        help_text="Email token provided by Paystack to disable/manage subscription.",
    )
    status = models.CharField(
        max_length=30,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
        help_text="Current subscription lifecycle status.",
    )
    current_period_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="End of the current paid billing period.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def student_cap(self):
        from .constants import SUBSCRIPTION_TIERS
        tier_info = SUBSCRIPTION_TIERS.get(self.tier, {})
        return tier_info.get("student_cap")

    def __str__(self):
        return f"{self.organization.name}: {self.tier} ({self.status})"



class BillingWebhookEvent(models.Model):
    """Processed Paystack webhook events for platform subscription billing.

    Ensures webhook events are processed at most once (idempotency).
    """

    event_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Unique event ID from Paystack webhook.",
    )
    event_type = models.CharField(
        max_length=100,
        help_text="Paystack event name (e.g. subscription.create, invoice.payment_failed).",
    )
    payload = models.JSONField(
        help_text="Raw payload received from Paystack.",
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at"]

    def __str__(self):
        return f"{self.event_type} ({self.event_id}) at {self.processed_at:%Y-%m-%d %H:%M}"
