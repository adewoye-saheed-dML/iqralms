from django.db import models
from accounts.models import User
from organizations.models import Organization

class AuditAction(models.TextChoices):
    # Authentication
    LOGIN_SUCCESS = "auth.login_success"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    PASSWORD_CHANGED = "auth.password_changed"
    PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"

    # Organization / membership
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_SETTINGS_UPDATED = "organization.settings_updated"
    MEMBERSHIP_CREATED = "membership.created"
    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"
    MEMBERSHIP_SUSPENDED = "membership.suspended"
    MEMBERSHIP_REACTIVATED = "membership.reactivated"

    # Permissions
    PERMISSION_CHANGED = "permission.changed"

    # Curriculum
    TRACK_CREATED = "curriculum.track_created"
    TRACK_UPDATED = "curriculum.track_updated"
    TRACK_ARCHIVED = "curriculum.track_archived"
    LEVEL_CREATED = "curriculum.level_created"
    LEVEL_UPDATED = "curriculum.level_updated"
    LEVEL_ARCHIVED = "curriculum.level_archived"
    TEACHER_ASSIGNMENT_CHANGED = "curriculum.teacher_assignment_changed"

    # Student / enrollment
    ENROLLMENT_CREATED = "enrollment.created"
    ENROLLMENT_UPDATED = "enrollment.updated"
    ENROLLMENT_WITHDRAWN = "enrollment.withdrawn"

    # Scheduling
    AVAILABILITY_CREATED = "availability.created"
    AVAILABILITY_UPDATED = "availability.updated"
    AVAILABILITY_DELETED = "availability.deleted"
    BOOKING_CREATED = "booking.created"
    BOOKING_UPDATED = "booking.updated"
    BOOKING_CANCELLED = "booking.cancelled"
    BOOKING_COMPLETED = "booking.completed"
    BOOKING_NO_SHOW = "booking.no_show"
    COHORT_CREATED = "cohort.created"
    COHORT_UPDATED = "cohort.updated"
    COHORT_CANCELLED = "cohort.cancelled"
    COHORT_ASSIGNMENT_CHANGED = "cohort.assignment_changed"

    # Assessment
    ASSESSMENT_CREATED = "assessment.created"
    ASSESSMENT_UPDATED = "assessment.updated"
    ASSESSMENT_FINALIZED = "assessment.finalized"
    ASSESSMENT_REOPENED = "assessment.reopened"

    # Pricing
    AGREEMENT_CREATED = "pricing.agreement_created"
    AGREEMENT_UPDATED = "pricing.agreement_updated"
    AGREEMENT_DEACTIVATED = "pricing.agreement_deactivated"
    RATE_CHANGED = "pricing.rate_changed"

    # Payouts
    PAYOUT_CREATED = "payout.created"
    PAYOUT_FINALIZED = "payout.finalized"
    PAYOUT_REOPENED = "payout.reopened"

    # Notifications
    NOTIFICATION_PROVIDER_UPDATED = "notification.provider_updated"
    NOTIFICATION_CONFIGURATION_UPDATED = "notification.configuration_updated"
    NOTIFICATION_SENT = "notification.sent"

    # Video
    VIDEO_PROVIDER_UPDATED = "video.provider_updated"
    MEETING_CREATED = "video.meeting_created"
    MEETING_UPDATED = "video.meeting_updated"
    MEETING_CANCELLED = "video.meeting_cancelled"

    # Bulk import
    BULK_IMPORT_STARTED = "bulk_import.started"
    BULK_IMPORT_VALIDATED = "bulk_import.validated"
    BULK_IMPORT_COMPLETED = "bulk_import.completed"
    BULK_IMPORT_FAILED = "bulk_import.failed"


class ActorType(models.TextChoices):
    USER = "USER"
    SYSTEM = "SYSTEM"


class AuditLog(models.Model):
    """
    Central, tenant-aware audit logging capability for sensitive academy administration
    and operational actions.
    """
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="audit_logs"
    )
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )
    actor_type = models.CharField(max_length=32, choices=ActorType.choices, default=ActorType.USER)
    actor_id_snapshot = models.CharField(max_length=128, blank=True, help_text="Preserves actor ID if the user is deleted")
    action = models.CharField(max_length=128, choices=AuditAction.choices)
    object_type = models.CharField(max_length=128, blank=True)
    object_id = models.CharField(max_length=128, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["organization", "action", "-created_at"]),
            models.Index(fields=["organization", "actor", "-created_at"]),
            models.Index(fields=["organization", "object_type", "object_id", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.action} on {self.object_type} ({self.object_id})"

    def save(self, *args, **kwargs):
        # Audit records are append-only.
        if self.pk is not None:
            raise ValueError("Audit logs are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit logs cannot be deleted.")

