"""Central service layer for notification creation, idempotency, delivery dispatch, and domain events."""

import logging
from typing import List, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone as dj_timezone

from organizations.models import MembershipStatus, OrganizationMembership, active_membership

from .adapters import BaseNotificationProvider, DeliveryResult, get_provider_for_channel
from .models import (
    DeliveryChannel,
    DeliveryStatus,
    EventType,
    Notification,
    NotificationDelivery,
)

logger = logging.getLogger(__name__)


class RecipientNotActiveInOrganization(ValidationError):
    """Raised when attempting to notify a user who lacks active membership in the academy."""
    pass


def deliver_notification(
    notification: Notification,
    channel: DeliveryChannel,
    provider: Optional[BaseNotificationProvider] = None,
) -> NotificationDelivery:
    """Execute a delivery attempt for a notification through a provider adapter.

    Invariants:
    - Provider failures are recorded on the delivery record.
    - Provider failures NEVER delete or mutate the notification.
    - Provider failures NEVER raise an exception to the caller.
    """
    if provider is None:
        try:
            provider = get_provider_for_channel(channel)
        except ValueError as exc:
            # Unsupported or unconfigured channel
            delivery = NotificationDelivery.objects.create(
                notification=notification,
                channel=channel,
                provider="unknown",
                status=DeliveryStatus.FAILED,
                attempt_count=1,
                error_code="UNSUPPORTED_CHANNEL",
                error_message=str(exc),
                attempted_at=dj_timezone.now(),
            )
            return delivery

    delivery = NotificationDelivery(
        notification=notification,
        channel=channel,
        provider=provider.provider_name,
        status=DeliveryStatus.PENDING,
        attempt_count=0,
    )
    delivery.save()

    attempt_time = dj_timezone.now()
    delivery.attempt_count += 1
    delivery.attempted_at = attempt_time

    try:
        result: DeliveryResult = provider.send(notification, delivery)
        if result.success:
            delivery.status = DeliveryStatus.SENT
            delivery.provider_message_id = result.provider_message_id
            delivery.delivered_at = dj_timezone.now()
            delivery.error_code = ""
            delivery.error_message = ""
        else:
            delivery.status = DeliveryStatus.FAILED
            delivery.error_code = result.error_code
            delivery.error_message = result.error_message
    except Exception as exc:
        logger.exception("Unexpected exception in provider adapter %s: %s", provider.provider_name, exc)
        delivery.status = DeliveryStatus.FAILED
        delivery.error_code = "PROVIDER_EXCEPTION"
        delivery.error_message = str(exc)

    delivery.save(
        update_fields=[
            "status",
            "attempt_count",
            "attempted_at",
            "delivered_at",
            "provider_message_id",
            "error_code",
            "error_message",
        ]
    )
    return delivery


def create_notification(
    *,
    organization,
    event_type: EventType,
    recipient,
    title: str,
    summary: str = "",
    payload: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
    channels: Optional[List[DeliveryChannel]] = None,
) -> Notification:
    """Create an academy-scoped notification with deterministic idempotency.

    Returns the created or existing Notification instance. The attribute
    ``_was_created`` is True if newly created, False if returned via idempotency.
    """
    if organization is None:
        raise ValidationError({"organization": "Organization is required."})

    # Validate recipient active membership
    membership = active_membership(user=recipient, organization=organization)
    if membership is None:
        raise RecipientNotActiveInOrganization(
            f"User '{recipient}' is not an active member of organization '{organization}'."
        )

    # Check idempotency key if provided
    if idempotency_key:
        existing = Notification.objects.filter(
            organization=organization, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            existing._was_created = False
            return existing

    clean_payload = dict(payload) if payload else {}

    with transaction.atomic():
        notification = Notification(
            organization=organization,
            event_type=event_type,
            recipient=recipient,
            title=title.strip(),
            summary=summary.strip(),
            payload=clean_payload,
            idempotency_key=idempotency_key,
        )
        notification.full_clean()
        notification.save()
        notification._was_created = True

    # Dispatch delivery attempts if requested
    if channels:
        for channel in channels:
            deliver_notification(notification=notification, channel=channel)

    return notification


# --- Domain Event Integrations (Tasks 8.1, 8.3, 8.6) -------------------------


def notify_booking_confirmed(
    booking,
    *,
    channels: Optional[List[DeliveryChannel]] = None,
) -> List[Notification]:
    """Emit BOOKING_CONFIRMED notifications for student, teacher, and authorized parent.

    Includes join link because recipient is authorized for this session.
    """
    org = booking.organization
    if not org:
        return []

    notifications = []
    student = booking.student
    teacher = booking.teacher

    base_payload = {
        "booking_id": booking.id,
        "student_id": student.id,
        "student_name": student.get_full_name() or student.username,
        "teacher_id": teacher.id,
        "teacher_name": teacher.get_full_name() or teacher.username,
        "track_name": booking.level.track.name,
        "level_name": booking.level.name,
        "start_time_utc": booking.start_time_utc.isoformat() if booking.start_time_utc else None,
        "duration_minutes": booking.duration_minutes,
        "video_join_url": booking.video_join_url,
    }

    # 1. Student notification
    if active_membership(user=student, organization=org):
        notif = create_notification(
            organization=org,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=student,
            title=f"Session Confirmed: {booking.level.track.name}",
            summary=(
                f"Your session with {base_payload['teacher_name']} is scheduled for "
                f"{booking.start_time_utc:%Y-%m-%d %H:%M} UTC."
            ),
            payload=base_payload,
            idempotency_key=f"booking:{booking.id}:confirmed:{student.id}",
            channels=channels,
        )
        notifications.append(notif)

    # 2. Linked active parents
    for link in getattr(student, "parent_links", []).all() if hasattr(student, "parent_links") else []:
        parent = link.parent
        if active_membership(user=parent, organization=org):
            notif = create_notification(
                organization=org,
                event_type=EventType.BOOKING_CONFIRMED,
                recipient=parent,
                title=f"Session Confirmed: {base_payload['student_name']}",
                summary=(
                    f"Session for {base_payload['student_name']} with {base_payload['teacher_name']} "
                    f"is scheduled for {booking.start_time_utc:%Y-%m-%d %H:%M} UTC."
                ),
                payload=base_payload,
                idempotency_key=f"booking:{booking.id}:confirmed:{parent.id}",
                channels=channels,
            )
            notifications.append(notif)

    # 3. Teacher notification
    if active_membership(user=teacher, organization=org):
        notif = create_notification(
            organization=org,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=teacher,
            title=f"New Session: {base_payload['student_name']}",
            summary=(
                f"You have a session with {base_payload['student_name']} ({base_payload['track_name']}) "
                f"at {booking.start_time_utc:%Y-%m-%d %H:%M} UTC."
            ),
            payload=base_payload,
            idempotency_key=f"booking:{booking.id}:confirmed:{teacher.id}",
            channels=channels,
        )
        notifications.append(notif)

    return notifications


def notify_booking_cancelled(
    booking,
    *,
    channels: Optional[List[DeliveryChannel]] = None,
) -> List[Notification]:
    """Emit BOOKING_CANCELLED notifications. Join link is strictly omitted."""
    org = booking.organization
    if not org:
        return []

    notifications = []
    student = booking.student
    teacher = booking.teacher

    cancel_payload = {
        "booking_id": booking.id,
        "student_id": student.id,
        "student_name": student.get_full_name() or student.username,
        "teacher_id": teacher.id,
        "teacher_name": teacher.get_full_name() or teacher.username,
        "start_time_utc": booking.start_time_utc.isoformat() if booking.start_time_utc else None,
        "cancelled_at": dj_timezone.now().isoformat(),
    }

    # 1. Student
    if active_membership(user=student, organization=org):
        notif = create_notification(
            organization=org,
            event_type=EventType.BOOKING_CANCELLED,
            recipient=student,
            title=f"Session Cancelled: {booking.level.track.name}",
            summary=(
                f"Your session with {cancel_payload['teacher_name']} scheduled for "
                f"{booking.start_time_utc:%Y-%m-%d %H:%M} UTC has been cancelled."
            ),
            payload=cancel_payload,
            idempotency_key=f"booking:{booking.id}:cancelled:{student.id}",
            channels=channels,
        )
        notifications.append(notif)

    # 2. Linked parents
    for link in getattr(student, "parent_links", []).all() if hasattr(student, "parent_links") else []:
        parent = link.parent
        if active_membership(user=parent, organization=org):
            notif = create_notification(
                organization=org,
                event_type=EventType.BOOKING_CANCELLED,
                recipient=parent,
                title=f"Session Cancelled: {cancel_payload['student_name']}",
                summary=(
                    f"The session for {cancel_payload['student_name']} on "
                    f"{booking.start_time_utc:%Y-%m-%d %H:%M} UTC has been cancelled."
                ),
                payload=cancel_payload,
                idempotency_key=f"booking:{booking.id}:cancelled:{parent.id}",
                channels=channels,
            )
            notifications.append(notif)

    # 3. Teacher
    if active_membership(user=teacher, organization=org):
        notif = create_notification(
            organization=org,
            event_type=EventType.BOOKING_CANCELLED,
            recipient=teacher,
            title=f"Session Cancelled: {cancel_payload['student_name']}",
            summary=(
                f"Your session with {cancel_payload['student_name']} on "
                f"{booking.start_time_utc:%Y-%m-%d %H:%M} UTC was cancelled."
            ),
            payload=cancel_payload,
            idempotency_key=f"booking:{booking.id}:cancelled:{teacher.id}",
            channels=channels,
        )
        notifications.append(notif)

    return notifications


def notify_placement_reviewed(
    placement,
    *,
    channels: Optional[List[DeliveryChannel]] = None,
) -> List[Notification]:
    """Emit PLACEMENT_REVIEWED notification when a placement is finalized.

    Excludes audio recordings, teacher private review notes, or QC fields.
    """
    org = placement.organization
    if not org:
        return []

    student = placement.student
    level = placement.recommended_level

    payload = {
        "placement_id": placement.id,
        "track_id": placement.track_id,
        "track_name": placement.track.name,
        "recommended_level_id": level.id if level else None,
        "recommended_level_name": level.name if level else None,
        "reviewed_at": placement.reviewed_at.isoformat() if placement.reviewed_at else None,
    }

    notifications = []
    if active_membership(user=student, organization=org):
        notif = create_notification(
            organization=org,
            event_type=EventType.PLACEMENT_REVIEWED,
            recipient=student,
            title=f"Placement Review Ready: {placement.track.name}",
            summary=(
                f"Your placement evaluation for {placement.track.name} is complete. "
                f"Recommended level: {payload['recommended_level_name'] or 'Assigned'}."
            ),
            payload=payload,
            idempotency_key=f"placement:{placement.id}:reviewed:{student.id}",
            channels=channels,
        )
        notifications.append(notif)

    for link in getattr(student, "parent_links", []).all() if hasattr(student, "parent_links") else []:
        parent = link.parent
        if active_membership(user=parent, organization=org):
            notif = create_notification(
                organization=org,
                event_type=EventType.PLACEMENT_REVIEWED,
                recipient=parent,
                title=f"Placement Review Ready: {student.get_full_name() or student.username}",
                summary=(
                    f"Placement evaluation for {student.get_full_name() or student.username} "
                    f"in {placement.track.name} is complete."
                ),
                payload=payload,
                idempotency_key=f"placement:{placement.id}:reviewed:{parent.id}",
                channels=channels,
            )
            notifications.append(notif)

    return notifications


def notify_progress_ready(
    snapshot_or_assessment,
    *,
    channels: Optional[List[DeliveryChannel]] = None,
) -> List[Notification]:
    """Emit PROGRESS_READY notification for student and parent.

    Internal QC notes, flag reasons, and lead private annotations are strictly stripped.
    """
    org = snapshot_or_assessment.organization
    if not org:
        return []

    student = snapshot_or_assessment.student
    is_snapshot = hasattr(snapshot_or_assessment, "period_start")

    if is_snapshot:
        snapshot = snapshot_or_assessment
        source_type = "snapshot"
        source_id = snapshot.id
        payload = {
            "progress_id": snapshot.id,
            "source_type": "snapshot",
            "track_id": snapshot.track_id,
            "track_name": snapshot.track.name,
            "period_start": snapshot.period_start.isoformat(),
            "period_end": snapshot.period_end.isoformat(),
            "summary": snapshot.summary,
        }
        title_student = f"Progress Report Ready: {snapshot.track.name}"
        summary_text = f"Your progress update for {snapshot.track.name} is now available."
    else:
        assessment = snapshot_or_assessment
        source_type = "assessment"
        source_id = assessment.id
        payload = {
            "progress_id": assessment.id,
            "source_type": "assessment",
            "track_id": assessment.track_id,
            "track_name": assessment.track.name,
            "assessed_at": assessment.assessed_at.isoformat(),
            "summary": assessment.teacher_summary,
        }
        title_student = f"Session Feedback Ready: {assessment.track.name}"
        summary_text = f"Feedback from your recent session in {assessment.track.name} is ready."

    notifications = []
    if active_membership(user=student, organization=org):
        notif = create_notification(
            organization=org,
            event_type=EventType.PROGRESS_READY,
            recipient=student,
            title=title_student,
            summary=summary_text,
            payload=payload,
            idempotency_key=f"progress:{source_type}:{source_id}:{student.id}",
            channels=channels,
        )
        notifications.append(notif)

    for link in getattr(student, "parent_links", []).all() if hasattr(student, "parent_links") else []:
        parent = link.parent
        if active_membership(user=parent, organization=org):
            notif = create_notification(
                organization=org,
                event_type=EventType.PROGRESS_READY,
                recipient=parent,
                title=f"Progress Update: {student.get_full_name() or student.username}",
                summary=(
                    f"A new progress update for {student.get_full_name() or student.username} "
                    f"in {payload['track_name']} is available."
                ),
                payload=payload,
                idempotency_key=f"progress:{source_type}:{source_id}:{parent.id}",
                channels=channels,
            )
            notifications.append(notif)

    return notifications


def notify_teacher_invitation(
    invitation: "organizations.models.OrganizationInvitation",
    *,
    channels: Optional[List[DeliveryChannel]] = None,
) -> None:
    """Send TEACHER_INVITATION email.

    Strictly excludes auth tokens, credentials, or passwords, but does include
    the invitation token needed to accept the invitation.
    """
    from django.core.mail import send_mail
    from django.conf import settings

    org = invitation.organization
    
    token = getattr(invitation, "raw_token", "REDACTED")
    subject = f"Invitation to join {org.name}"
    message = f"You have been invited to join {org.name} as a {invitation.role}.\n\nYour invitation token is: {token}"

    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[invitation.email],
        fail_silently=True,
    )
