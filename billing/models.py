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
    onboarding_email_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the creator/head onboarding video guide email was sent.",
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


class SubscriberOnboardingDispatch(models.Model):
    """Log of onboarding guide emails sent by the platform creator to academy subscribers."""

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="onboarding_dispatches",
        help_text="The academy whose subscriber/owner was notified.",
    )
    recipient_email = models.EmailField(
        help_text="Email address the onboarding guide was sent to.",
    )
    subject = models.CharField(
        max_length=255,
        default="Welcome to Iqra LMS: Complete Academy Setup Guide & Video Walkthroughs",
    )
    sent_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_onboarding_emails",
        help_text="Platform admin or creator who initiated the onboarding dispatch.",
    )
    video_links = models.JSONField(
        default=list,
        blank=True,
        help_text="List of guide video objects included in this dispatch.",
    )
    custom_message = models.TextField(
        blank=True,
        default="",
        help_text="Personalized note or instructions from the platform creator.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Onboarding dispatch to {self.recipient_email} at {self.created_at}"


class PlatformAnnouncement(models.Model):
    """Global system or curriculum announcements from the platform head."""

    title = models.CharField(max_length=200)
    message = models.TextField()
    is_active = models.BooleanField(default=True)
    level = models.CharField(
        max_length=20,
        choices=[("info", "Information"), ("warning", "Warning"), ("success", "Success")],
        default="info",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="announcements_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Announcement: {self.title} ({'Active' if self.is_active else 'Inactive'})"


class PlatformSystemConfig(models.Model):
    """Global platform-wide infrastructure and provider configuration managed by the Platform Owner."""

    video_provider = models.CharField(
        max_length=32,
        choices=[
            ("daily", "Daily.co (In-App Video & Cloud Recording)"),
            ("jitsi", "Jitsi Meet (Self-Hosted VPS / Open-Source)"),
        ],
        default="daily",
        help_text="Active live classroom video provider across all academies.",
    )
    jitsi_domain = models.CharField(
        max_length=255,
        default="meet.jit.si",
        help_text="Custom domain for self-hosted Jitsi Meet VPS, e.g. meet.yourvps.com or jitsi.iqralms.com",
    )
    daily_domain = models.CharField(
        max_length=255,
        default="api.daily.co",
        help_text="Daily.co API domain endpoint.",
    )
    daily_api_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional custom Daily.co API key override.",
    )
    daily_enable_recording = models.BooleanField(
        default=True,
        help_text="Enable cloud recording for Daily.co rooms to drop in academy owner dashboards.",
    )
    updated_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="platform_configs_updated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform System Config"
        verbose_name_plural = "Platform System Config"

    @classmethod
    def get_current(cls):
        config = cls.objects.first()
        if not config:
            from django.conf import settings
            config = cls.objects.create(
                video_provider=getattr(settings, "DEFAULT_VIDEO_PROVIDER", "daily"),
                jitsi_domain=getattr(settings, "JITSI_DOMAIN", "meet.jit.si"),
                daily_domain=getattr(settings, "DAILY_API_DOMAIN", "api.daily.co"),
                daily_enable_recording=getattr(settings, "DAILY_ENABLE_RECORDING", True),
            )
        return config

    def __str__(self):
        return f"Platform System Config (Provider: {self.video_provider})"
