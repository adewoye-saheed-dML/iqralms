"""Model, constraint and tenant-integrity tests for notifications and deliveries."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.tests.factories import UserFactory
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
from organizations.tests.factories import OrganizationFactory

from notifications.models import (
    DeliveryChannel,
    DeliveryStatus,
    EventType,
    Notification,
    NotificationDelivery,
)
from notifications.tests.factories import NotificationDeliveryFactory, NotificationFactory


class NotificationModelTests(TestCase):
    def setUp(self):
        self.org1 = OrganizationFactory(name="Academy One")
        self.org2 = OrganizationFactory(name="Academy Two")
        self.user1 = UserFactory(username="student1")
        self.user2 = UserFactory(username="student2")

        self.m1 = OrganizationMembership.objects.create(
            organization=self.org1,
            user=self.user1,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )
        self.m2 = OrganizationMembership.objects.create(
            organization=self.org2,
            user=self.user2,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )

    def test_valid_notification_creation(self):
        notif = Notification.objects.create(
            organization=self.org1,
            recipient=self.user1,
            event_type=EventType.BOOKING_CONFIRMED,
            title="Session Confirmed",
            summary="Your session is scheduled.",
            payload={"booking_id": 1, "duration_minutes": 30},
            idempotency_key="booking:1:confirmed:user1",
        )
        self.assertFalse(notif.is_read)
        self.assertIsNone(notif.read_at)
        self.assertEqual(notif.organization, self.org1)

    def test_empty_title_rejected(self):
        notif = Notification(
            organization=self.org1,
            recipient=self.user1,
            event_type=EventType.BOOKING_CONFIRMED,
            title="   ",
            summary="Body",
        )
        with self.assertRaises(ValidationError) as ctx:
            notif.clean()
        self.assertIn("title", ctx.exception.message_dict)

    def test_recipient_must_be_active_member_of_organization(self):
        # user2 is not a member of org1
        notif = Notification(
            organization=self.org1,
            recipient=self.user2,
            event_type=EventType.BOOKING_CONFIRMED,
            title="Cross-tenant recipient",
        )
        with self.assertRaises(ValidationError) as ctx:
            notif.clean()
        self.assertIn("recipient", ctx.exception.message_dict)

    def test_suspended_member_cannot_be_recipient(self):
        suspended_user = UserFactory(username="suspended_user")
        OrganizationMembership.objects.create(
            organization=self.org1,
            user=suspended_user,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.SUSPENDED,
        )
        notif = Notification(
            organization=self.org1,
            recipient=suspended_user,
            event_type=EventType.BOOKING_CONFIRMED,
            title="Suspended Member Recipient",
        )
        with self.assertRaises(ValidationError) as ctx:
            notif.clean()
        self.assertIn("recipient", ctx.exception.message_dict)

    def test_forbidden_sensitive_keys_in_payload_rejected(self):
        forbidden_keys = [
            "password",
            "token",
            "access_token",
            "api_key",
            "secret",
            "flag_reason",
            "lead_review_note",
            "internal_notes",
            "qc_note",
            "rate_used",
            "amount_paid",
        ]
        for key in forbidden_keys:
            with self.subTest(key=key):
                notif = Notification(
                    organization=self.org1,
                    recipient=self.user1,
                    event_type=EventType.PROGRESS_READY,
                    title="Progress Update",
                    payload={"valid_key": 123, key: "leaked_secret"},
                )
                with self.assertRaises(ValidationError) as ctx:
                    notif.clean()
                self.assertIn("payload", ctx.exception.message_dict)

    def test_nested_forbidden_keys_in_payload_rejected(self):
        notif = Notification(
            organization=self.org1,
            recipient=self.user1,
            event_type=EventType.PROGRESS_READY,
            title="Progress Update",
            payload={"nested": {"qc_notes": "internal qc"}},
        )
        with self.assertRaises(ValidationError) as ctx:
            notif.clean()
        self.assertIn("payload", ctx.exception.message_dict)

    def test_mark_as_read(self):
        notif = NotificationFactory(organization=self.org1, recipient=self.user1)
        self.assertFalse(notif.is_read)
        notif.mark_as_read()
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertIsNotNone(notif.read_at)

    def test_idempotency_key_uniqueness_per_organization(self):
        Notification.objects.create(
            organization=self.org1,
            recipient=self.user1,
            event_type=EventType.BOOKING_CONFIRMED,
            title="First",
            idempotency_key="key-123",
        )
        # Duplicate key in SAME organization raises ValidationError or IntegrityError
        with self.assertRaises((ValidationError, IntegrityError)):
            Notification.objects.create(
                organization=self.org1,
                recipient=self.user1,
                event_type=EventType.BOOKING_CONFIRMED,
                title="Duplicate",
                idempotency_key="key-123",
            )

        # Same key in DIFFERENT organization is allowed
        other_notif = Notification.objects.create(
            organization=self.org2,
            recipient=self.user2,
            event_type=EventType.BOOKING_CONFIRMED,
            title="Other Org",
            idempotency_key="key-123",
        )
        self.assertIsNotNone(other_notif.pk)

    def test_querysets_in_organization_and_for_recipient(self):
        n1 = NotificationFactory(organization=self.org1, recipient=self.user1)
        n2 = NotificationFactory(organization=self.org2, recipient=self.user2)

        org1_notifs = Notification.objects.in_organization(self.org1)
        self.assertIn(n1, org1_notifs)
        self.assertNotIn(n2, org1_notifs)

        user1_notifs = Notification.objects.for_recipient(self.user1)
        self.assertIn(n1, user1_notifs)
        self.assertNotIn(n2, user1_notifs)

        # Unread filter
        self.assertIn(n1, Notification.objects.unread())
        n1.mark_as_read()
        self.assertNotIn(n1, Notification.objects.unread())
        self.assertIn(n1, Notification.objects.read())


class NotificationDeliveryModelTests(TestCase):
    def setUp(self):
        self.org1 = OrganizationFactory(name="Academy One")
        self.user1 = UserFactory(username="student1")
        self.m1 = OrganizationMembership.objects.create(
            organization=self.org1,
            user=self.user1,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )
        self.notif = NotificationFactory(organization=self.org1, recipient=self.user1)

    def test_valid_delivery_creation(self):
        delivery = NotificationDelivery.objects.create(
            notification=self.notif,
            channel=DeliveryChannel.EMAIL,
            provider="email_smtp",
            status=DeliveryStatus.SENT,
            attempt_count=1,
            provider_message_id="msg-abc-123",
        )
        self.assertEqual(delivery.organization, self.org1)
        self.assertEqual(delivery.status, DeliveryStatus.SENT)

    def test_delivery_requires_channel_and_provider(self):
        d1 = NotificationDelivery(
            notification=self.notif,
            channel="",
            provider="email_smtp",
        )
        with self.assertRaises(ValidationError) as ctx:
            d1.clean()
        self.assertIn("channel", ctx.exception.message_dict)

        d2 = NotificationDelivery(
            notification=self.notif,
            channel=DeliveryChannel.EMAIL,
            provider="  ",
        )
        with self.assertRaises(ValidationError) as ctx:
            d2.clean()
        self.assertIn("provider", ctx.exception.message_dict)

    def test_delivery_queryset_in_organization(self):
        org2 = OrganizationFactory(name="Academy Two")
        user2 = UserFactory(username="student2")
        OrganizationMembership.objects.create(
            organization=org2,
            user=user2,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )
        notif2 = NotificationFactory(organization=org2, recipient=user2)

        deliv1 = NotificationDeliveryFactory(notification=self.notif)
        deliv2 = NotificationDeliveryFactory(notification=notif2)

        org1_deliveries = NotificationDelivery.objects.in_organization(self.org1)
        self.assertIn(deliv1, org1_deliveries)
        self.assertNotIn(deliv2, org1_deliveries)
