"""Tests for notification service layer, idempotency, deliveries, and domain event helpers."""

from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.tests.factories import SubTeacherFactory, UserFactory
from assessment.models import ProgressSnapshot, SessionAssessment
from assessment.tests.factories import SessionAssessmentFactory
from curriculum.models import PlacementResult, Status
from curriculum.tests.factories import TrackFactory, TrackWithLevelsFactory
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
from organizations.tests.factories import OrganizationFactory
from scheduling.models import BookingStatus
from scheduling.tests.factories import BookingFactory

from notifications.adapters import MockFailingProvider
from notifications.models import (
    DeliveryChannel,
    DeliveryStatus,
    EventType,
    Notification,
    NotificationDelivery,
)
from notifications.services import (
    RecipientNotActiveInOrganization,
    create_notification,
    deliver_notification,
    notify_booking_cancelled,
    notify_booking_confirmed,
    notify_placement_reviewed,
    notify_progress_ready,
    notify_teacher_invitation,
)


class NotificationServiceTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory(name="Al-Furqan Academy")
        self.student = UserFactory(username="student_user")
        self.teacher = UserFactory(username="teacher_user")
        self.parent = UserFactory(username="parent_user")

        self.m_student = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.student,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )
        self.m_teacher = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.teacher,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.ACTIVE,
        )

    def test_create_notification_success(self):
        notif = create_notification(
            organization=self.org,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=self.student,
            title="Session Confirmed",
            summary="You are scheduled.",
            payload={"booking_id": 42},
            idempotency_key="book:42:student",
        )
        self.assertTrue(getattr(notif, "_was_created", False))
        self.assertEqual(notif.organization, self.org)
        self.assertEqual(notif.recipient, self.student)
        self.assertEqual(Notification.objects.count(), 1)

    def test_create_notification_requires_active_membership(self):
        outsider = UserFactory(username="outsider")
        with self.assertRaises(RecipientNotActiveInOrganization):
            create_notification(
                organization=self.org,
                event_type=EventType.BOOKING_CONFIRMED,
                recipient=outsider,
                title="Invalid Recipient",
            )
        self.assertEqual(Notification.objects.count(), 0)

    def test_idempotency_returns_existing_without_duplicate(self):
        notif1 = create_notification(
            organization=self.org,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=self.student,
            title="First Attempt",
            idempotency_key="unique-idemp-key",
        )
        self.assertTrue(getattr(notif1, "_was_created", False))

        # Second call with same idempotency key
        notif2 = create_notification(
            organization=self.org,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=self.student,
            title="Second Attempt",
            idempotency_key="unique-idemp-key",
        )
        self.assertFalse(getattr(notif2, "_was_created", True))
        self.assertEqual(notif1.pk, notif2.pk)
        self.assertEqual(Notification.objects.count(), 1)

    def test_deliver_notification_success_and_failure_isolation(self):
        notif = create_notification(
            organization=self.org,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=self.student,
            title="Deliver Test",
        )

        # 1. In-app delivery succeeds
        delivery_in_app = deliver_notification(notif, DeliveryChannel.IN_APP)
        self.assertEqual(delivery_in_app.status, DeliveryStatus.SENT)
        self.assertEqual(delivery_in_app.attempt_count, 1)

        # 2. Failing provider simulation
        failing_provider = MockFailingProvider(
            error_code="SERVICE_UNAVAILABLE",
            error_message="Gateway timeout",
        )
        delivery_failing = deliver_notification(
            notif, DeliveryChannel.EMAIL, provider=failing_provider
        )
        self.assertEqual(delivery_failing.status, DeliveryStatus.FAILED)
        self.assertEqual(delivery_failing.error_code, "SERVICE_UNAVAILABLE")
        self.assertEqual(delivery_failing.error_message, "Gateway timeout")
        self.assertEqual(delivery_failing.attempt_count, 1)

        # Invariant: Notification still exists and unaffected
        notif.refresh_from_db()
        self.assertEqual(notif.deliveries.count(), 2)
        # In-app delivery remains independent
        delivery_in_app.refresh_from_db()
        self.assertEqual(delivery_in_app.status, DeliveryStatus.SENT)


class DomainEventHelpersTests(TestCase):
    def setUp(self):
        self.org = OrganizationFactory(name="Academy Al-Iman")
        self.track = TrackWithLevelsFactory(organization=self.org, levels=2)
        self.student = UserFactory(username="student_amina")

        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.student,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )

    def _admit_booking_parties(self, booking):
        org = booking.organization
        OrganizationMembership.objects.get_or_create(
            organization=org,
            user=booking.student,
            defaults={"role": OrganizationRole.STAFF, "status": MembershipStatus.ACTIVE},
        )
        OrganizationMembership.objects.get_or_create(
            organization=org,
            user=booking.teacher,
            defaults={"role": OrganizationRole.TEACHER, "status": MembershipStatus.ACTIVE},
        )

    def test_notify_booking_confirmed(self):
        booking = BookingFactory(status=BookingStatus.SCHEDULED)
        self._admit_booking_parties(booking)

        notifs = notify_booking_confirmed(booking)
        self.assertEqual(len(notifs), 2)  # 1 student + 1 teacher

        student_notif = next(n for n in notifs if n.recipient == booking.student)
        self.assertEqual(student_notif.event_type, EventType.BOOKING_CONFIRMED)
        self.assertIn("video_join_url", student_notif.payload)
        self.assertEqual(student_notif.payload["video_join_url"], booking.video_join_url)

        teacher_notif = next(n for n in notifs if n.recipient == booking.teacher)
        self.assertEqual(teacher_notif.event_type, EventType.BOOKING_CONFIRMED)
        self.assertIn("video_join_url", teacher_notif.payload)

    def test_notify_booking_cancelled_omits_join_url(self):
        booking = BookingFactory(status=BookingStatus.CANCELLED)
        self._admit_booking_parties(booking)

        notifs = notify_booking_cancelled(booking)
        self.assertEqual(len(notifs), 2)

        for n in notifs:
            self.assertEqual(n.event_type, EventType.BOOKING_CANCELLED)
            self.assertNotIn("video_join_url", n.payload)
            self.assertIn("cancelled_at", n.payload)

    def test_notify_placement_reviewed_privacy(self):
        placement = PlacementResult.objects.create(
            student=self.student,
            track=self.track,
            skipped_as_beginner=True,
            status=Status.REVIEWED,
            reviewed_at=dj_timezone.now(),
        )
        notifs = notify_placement_reviewed(placement)
        self.assertEqual(len(notifs), 1)
        notif = notifs[0]
        self.assertEqual(notif.event_type, EventType.PLACEMENT_REVIEWED)
        self.assertEqual(notif.recipient, self.student)
        # Payload must not contain audio or internal fields
        self.assertNotIn("audio_sample", notif.payload)
        self.assertNotIn("qc_note", notif.payload)

    def test_notify_progress_ready_excludes_qc_fields(self):
        assessment = SessionAssessmentFactory(
            flagged_for_review=True,
            flag_reason="Internal QC check needed",
            lead_review_note="Internal supervisor private note",
            teacher_summary="Great recitation today.",
        )
        org = assessment.organization
        OrganizationMembership.objects.get_or_create(
            organization=org,
            user=assessment.student,
            defaults={"role": OrganizationRole.STAFF, "status": MembershipStatus.ACTIVE},
        )

        notifs = notify_progress_ready(assessment)
        self.assertEqual(len(notifs), 1)
        notif = notifs[0]
        self.assertEqual(notif.event_type, EventType.PROGRESS_READY)
        self.assertEqual(notif.recipient, assessment.student)
        # Verify strict privacy compliance
        self.assertNotIn("flag_reason", notif.payload)
        self.assertNotIn("lead_review_note", notif.payload)
        self.assertEqual(notif.payload["summary"], "Great recitation today.")

    def test_notify_teacher_invitation_excludes_credentials(self):
        from organizations.models import OrganizationInvitation, OrganizationRole
        invitation, token = OrganizationInvitation.generate_token_and_digest()
        import datetime
        from django.utils import timezone
        
        inv_obj = OrganizationInvitation.objects.create(
            organization=self.org,
            email="newteacher@example.com",
            role=OrganizationRole.TEACHER,
            token_digest=token,
            expires_at=timezone.now() + datetime.timedelta(days=7)
        )
        inv_obj.raw_token = token
        
        from django.core import mail
        mail.outbox = []
        
        notify_teacher_invitation(inv_obj)
        
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("newteacher@example.com", email.to)
        self.assertIn(token, email.body)
        self.assertNotIn("password", email.body)
