"""The central notification domain: academy-aware events and provider-neutral delivery.

Architecture:
    Domain action -> Notification event -> Notification service -> Provider adapter -> Delivery record

Tenant safety rules:
1. Every notification has deterministic academy ownership via ``organization``.
2. Recipient must be an active member of the academy at creation time.
3. Delivery records belong to the notification and inherit its academy ownership.
4. Notification and delivery cannot cross academies.
5. Payloads are strictly stripped of private credentials, secrets, QC notes, or teacher financial data.
6. Idempotency is enforced per academy + idempotency key.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone as dj_timezone

from organizations.models import Organization, active_membership


class EventType(models.TextChoices):
    """The initial required SaaS domain events."""

    BOOKING_CONFIRMED = "booking_confirmed", "Booking Confirmed"
    BOOKING_CANCELLED = "booking_cancelled", "Booking Cancelled"
    PLACEMENT_REVIEWED = "placement_reviewed", "Placement Reviewed"
    PROGRESS_READY = "progress_ready", "Progress Ready"
    TEACHER_INVITATION = "teacher_invitation", "Teacher Invitation"


class DeliveryChannel(models.TextChoices):
    """Delivery channels supported across the platform boundary."""

    EMAIL = "email", "Email"
    WHATSAPP = "whatsapp", "WhatsApp"
    TELEGRAM = "telegram", "Telegram"
    PUSH = "push", "Push"
    IN_APP = "in_app", "In-App"


class DeliveryStatus(models.TextChoices):
    """Lifecycle status of a delivery attempt."""

    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"


#: Sensitive keys that must NEVER be stored in notification payloads (Task 8.2 & Privacy rules).
FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "password",
    "secret",
    "token",
    "access_token",
    "api_key",
    "auth_token",
    "credential",
    "credentials",
    "qc_note",
    "qc_notes",
    "internal_notes",
    "internal_qc",
    "flag_reason",
    "lead_review_note",
    "rate_used",
    "amount_paid",
    "teacher_financial_data",
})


def _scan_for_forbidden_keys(payload_obj):
    """Recursively check for forbidden keys in a payload structure."""
    if isinstance(payload_obj, dict):
        for key, value in payload_obj.items():
            if str(key).lower() in FORBIDDEN_PAYLOAD_KEYS:
                return str(key)
            nested = _scan_for_forbidden_keys(value)
            if nested:
                return nested
    elif isinstance(payload_obj, (list, tuple, set)):
        for item in payload_obj:
            nested = _scan_for_forbidden_keys(item)
            if nested:
                return nested
    return None


class NotificationQuerySet(models.QuerySet):
    """Tenant-scoped and recipient-scoped queryset helpers for notifications."""

    def in_organization(self, organization):
        if organization is None:
            return self.none()
        org_id = getattr(organization, "pk", organization)
        return self.filter(organization_id=org_id)

    def for_recipient(self, recipient):
        if recipient is None:
            return self.none()
        recipient_id = getattr(recipient, "pk", recipient)
        return self.filter(recipient_id=recipient_id)

    def unread(self):
        return self.filter(read_at__isnull=True)

    def read(self):
        return self.filter(read_at__isnull=False)


class Notification(models.Model):
    """A tenant-owned notification event directed at one valid academy recipient."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text="The academy this notification belongs to.",
    )
    event_type = models.CharField(
        max_length=32,
        choices=EventType.choices,
        db_index=True,
        help_text="The event type triggering this notification.",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text="The recipient of this notification.",
    )
    title = models.CharField(
        max_length=200,
        help_text="Short headline summary.",
    )
    summary = models.TextField(
        blank=True,
        help_text="Human-readable notification text.",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON-serializable domain payload stripped of secrets or provider details.",
    )
    idempotency_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text="Academy-scoped idempotency key preventing duplicate notifications.",
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when recipient marked this notification read.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=Q(idempotency_key__isnull=False),
                name="unique_notification_org_idempotency_key",
            ),
        ]

    def __str__(self):
        return (
            f"[{self.organization.name}] {self.event_type} -> "
            f"{self.recipient.username} ({'read' if self.is_read else 'unread'})"
        )

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def mark_as_read(self):
        """Mark this notification as read by the recipient."""
        if self.read_at is None:
            self.read_at = dj_timezone.now()
            self.save(update_fields=["read_at"])

    def clean(self):
        super().clean()
        errors = {}

        if not self.title or not self.title.strip():
            errors["title"] = "Title cannot be empty."

        # Tenant integrity: recipient must be valid for the academy at creation
        if self._state.adding and self.organization_id and self.recipient_id:
            membership = active_membership(
                user=self.recipient, organization=self.organization
            )
            if membership is None:
                errors["recipient"] = (
                    "Recipient must be an active member of this organization."
                )

        # Privacy integrity: forbidden/secret keys scan
        if self.payload:
            forbidden_key = _scan_for_forbidden_keys(self.payload)
            if forbidden_key:
                errors["payload"] = (
                    f"Payload contains forbidden sensitive key: '{forbidden_key}'."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Enforce invariants on direct ORM writes as well as API
        self.full_clean()
        super().save(*args, **kwargs)


class NotificationDeliveryQuerySet(models.QuerySet):
    """Tenant-scoped queryset helper for deliveries."""

    def in_organization(self, organization):
        if organization is None:
            return self.none()
        org_id = getattr(organization, "pk", organization)
        return self.filter(notification__organization_id=org_id)


class NotificationDelivery(models.Model):
    """An auditable delivery attempt of a notification through a provider channel."""

    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="deliveries",
        help_text="The notification being delivered.",
    )
    channel = models.CharField(
        max_length=32,
        choices=DeliveryChannel.choices,
        db_index=True,
        help_text="Channel used for delivery.",
    )
    provider = models.CharField(
        max_length=64,
        help_text="Provider adapter identifier (e.g., 'email_smtp', 'meta_whatsapp').",
    )
    status = models.CharField(
        max_length=32,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
        db_index=True,
        help_text="Delivery status outcome.",
    )
    attempt_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of delivery attempts executed.",
    )
    provider_message_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Identifier returned by the external provider.",
    )
    error_code = models.CharField(
        max_length=64,
        blank=True,
        help_text="Machine-readable error code if failed.",
    )
    error_message = models.TextField(
        blank=True,
        help_text="Human-readable error details if failed.",
    )
    attempted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the latest delivery attempt.",
    )
    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when delivery was confirmed.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    objects = NotificationDeliveryQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return (
            f"Delivery #{self.pk} for Notification #{self.notification_id} "
            f"via {self.channel} ({self.status})"
        )

    @property
    def organization(self):
        """The academy this delivery belongs to, derived from its notification."""
        if self.notification_id and hasattr(self, "notification"):
            return self.notification.organization
        return None

    def clean(self):
        super().clean()
        errors = {}
        if not self.notification_id:
            errors["notification"] = "Delivery must belong to a valid notification."
        if not self.channel:
            errors["channel"] = "Channel cannot be empty."
        if not self.provider or not self.provider.strip():
            errors["provider"] = "Provider identifier cannot be empty."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
