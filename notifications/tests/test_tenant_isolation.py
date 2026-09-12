"""Adversarial tenant-isolation and privacy test suite for the notification domain."""

from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import LeadTeacherFactory, ParentFactory, StudentFactory, SubTeacherFactory, UserFactory
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
from organizations.tests.factories import OrganizationFactory

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
)
from notifications.tests.factories import NotificationDeliveryFactory, NotificationFactory


class NotificationTenantIsolationTests(APITestCase):
    def setUp(self):
        # Academy A
        self.org_a = OrganizationFactory(name="Academy Alpha")
        self.owner_a = UserFactory(username="owner_alpha")
        self.admin_a = UserFactory(username="admin_alpha")
        self.teacher_a1 = SubTeacherFactory(username="teacher_a1")
        self.teacher_a2 = SubTeacherFactory(username="teacher_a2")
        self.student_a1 = StudentFactory(username="student_a1")
        self.student_a2 = StudentFactory(username="student_a2")
        self.parent_a = ParentFactory(username="parent_a")
        self.suspended_a = UserFactory(username="suspended_alpha")

        # Academy B
        self.org_b = OrganizationFactory(name="Academy Beta")
        self.owner_b = UserFactory(username="owner_beta")
        self.admin_b = UserFactory(username="admin_beta")
        self.teacher_b = SubTeacherFactory(username="teacher_b")
        self.student_b = StudentFactory(username="student_b")
        self.parent_b = ParentFactory(username="parent_b")

        # Outside user
        self.outsider = UserFactory(username="complete_outsider")

        # Org A Memberships
        for u, r, s in [
            (self.owner_a, OrganizationRole.OWNER, MembershipStatus.ACTIVE),
            (self.admin_a, OrganizationRole.ADMIN, MembershipStatus.ACTIVE),
            (self.teacher_a1, OrganizationRole.TEACHER, MembershipStatus.ACTIVE),
            (self.teacher_a2, OrganizationRole.TEACHER, MembershipStatus.ACTIVE),
            (self.student_a1, OrganizationRole.STAFF, MembershipStatus.ACTIVE),
            (self.student_a2, OrganizationRole.STAFF, MembershipStatus.ACTIVE),
            (self.parent_a, OrganizationRole.STAFF, MembershipStatus.ACTIVE),
            (self.suspended_a, OrganizationRole.STAFF, MembershipStatus.SUSPENDED),
        ]:
            OrganizationMembership.objects.create(organization=self.org_a, user=u, role=r, status=s)

        # Org B Memberships
        for u, r, s in [
            (self.owner_b, OrganizationRole.OWNER, MembershipStatus.ACTIVE),
            (self.admin_b, OrganizationRole.ADMIN, MembershipStatus.ACTIVE),
            (self.teacher_b, OrganizationRole.TEACHER, MembershipStatus.ACTIVE),
            (self.student_b, OrganizationRole.STAFF, MembershipStatus.ACTIVE),
            (self.parent_b, OrganizationRole.STAFF, MembershipStatus.ACTIVE),
        ]:
            OrganizationMembership.objects.create(organization=self.org_b, user=u, role=r, status=s)

        # Seed Notifications
        self.notif_a1 = NotificationFactory(
            organization=self.org_a,
            recipient=self.student_a1,
            title="Notification A1",
            idempotency_key="org_a:notif_1",
        )
        self.notif_a2 = NotificationFactory(
            organization=self.org_a,
            recipient=self.student_a2,
            title="Notification A2",
            idempotency_key="org_a:notif_2",
        )
        self.notif_teacher_a = NotificationFactory(
            organization=self.org_a,
            recipient=self.teacher_a1,
            title="Teacher A1 Notification",
            idempotency_key="org_a:teacher_1",
        )

        self.notif_b = NotificationFactory(
            organization=self.org_b,
            recipient=self.student_b,
            title="Notification B",
            idempotency_key="org_b:notif_b",
        )
        self.notif_teacher_b = NotificationFactory(
            organization=self.org_b,
            recipient=self.teacher_b,
            title="Teacher B Notification",
            idempotency_key="org_b:teacher_b",
        )

        # Seed Deliveries
        self.deliv_a = NotificationDeliveryFactory(
            notification=self.notif_a1,
            channel=DeliveryChannel.EMAIL,
            provider="email_smtp",
        )
        self.deliv_b = NotificationDeliveryFactory(
            notification=self.notif_b,
            channel=DeliveryChannel.EMAIL,
            provider="email_smtp",
        )

    def _get_results(self, resp):
        if isinstance(resp.data, list):
            return resp.data
        return resp.data.get("results", [])

    # --- 1. Tenant Isolation Tests ---

    def test_academy_a_admin_cannot_list_academy_b_notifications(self):
        self.client.force_authenticate(user=self.admin_a)
        url = reverse("notifications:notification-admin-list", kwargs={"organization_pk": self.org_b.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_academy_a_admin_cannot_inspect_academy_b_deliveries(self):
        self.client.force_authenticate(user=self.admin_a)
        url = reverse("notifications:delivery-admin-list", kwargs={"organization_pk": self.org_b.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_known_notification_id_from_b_cannot_be_read_by_academy_a_admin(self):
        self.client.force_authenticate(user=self.admin_a)
        # Attempting to fetch notification B via Org A URL kwarg
        url = reverse(
            "notifications:notification-detail",
            kwargs={"organization_pk": self.org_a.pk, "pk": self.notif_b.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_known_notification_id_from_b_cannot_be_read_by_academy_a_student(self):
        self.client.force_authenticate(user=self.student_a1)
        url = reverse(
            "notifications:notification-detail",
            kwargs={"organization_pk": self.org_a.pk, "pk": self.notif_b.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- 2. Recipient Isolation Tests ---

    def test_teacher_a_cannot_read_teacher_b_notifications(self):
        self.client.force_authenticate(user=self.teacher_a1)
        url = reverse(
            "notifications:notification-detail",
            kwargs={"organization_pk": self.org_a.pk, "pk": self.notif_teacher_b.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_a1_cannot_read_teacher_a2_notifications(self):
        notif_teacher_a2 = NotificationFactory(
            organization=self.org_a,
            recipient=self.teacher_a2,
            title="Teacher A2 Notification",
        )
        self.client.force_authenticate(user=self.teacher_a1)
        url = reverse(
            "notifications:notification-detail",
            kwargs={"organization_pk": self.org_a.pk, "pk": notif_teacher_a2.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_a1_cannot_read_student_a2_notifications(self):
        self.client.force_authenticate(user=self.student_a1)
        url = reverse(
            "notifications:notification-detail",
            kwargs={"organization_pk": self.org_a.pk, "pk": self.notif_a2.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_parent_a_cannot_read_parent_b_notifications(self):
        notif_parent_b = NotificationFactory(
            organization=self.org_b,
            recipient=self.parent_b,
            title="Parent B Notice",
        )
        self.client.force_authenticate(user=self.parent_a)
        url = reverse(
            "notifications:notification-detail",
            kwargs={"organization_pk": self.org_a.pk, "pk": notif_parent_b.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- 3. Membership Status Enforcement ---

    def test_suspended_member_denied_access(self):
        self.client.force_authenticate(user=self.suspended_a)
        url = reverse("notifications:notification-mine", kwargs={"organization_pk": self.org_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_member_denied_access(self):
        self.client.force_authenticate(user=self.outsider)
        url = reverse("notifications:notification-mine", kwargs={"organization_pk": self.org_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # --- 4. Cross-Tenant Creation Rejection ---

    def test_reject_cross_tenant_recipient_at_service_layer(self):
        # Attempt to create an Org A notification targeting Org B's student
        with self.assertRaises(RecipientNotActiveInOrganization):
            create_notification(
                organization=self.org_a,
                event_type=EventType.BOOKING_CONFIRMED,
                recipient=self.student_b,
                title="Cross Tenant Attack",
            )
        self.assertFalse(
            Notification.objects.filter(
                organization=self.org_a, recipient=self.student_b
            ).exists()
        )

    # --- 5. Idempotency Invariants ---

    def test_idempotency_booking_confirmation_repeated_safe(self):
        idemp_key = "booking:100:confirmed:student_a1"
        notif1 = create_notification(
            organization=self.org_a,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=self.student_a1,
            title="Booking 100 Confirmed",
            idempotency_key=idemp_key,
        )
        self.assertTrue(getattr(notif1, "_was_created", False))

        # Repeated confirmation request
        notif2 = create_notification(
            organization=self.org_a,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=self.student_a1,
            title="Booking 100 Confirmed Repeated",
            idempotency_key=idemp_key,
        )
        self.assertFalse(getattr(notif2, "_was_created", True))
        self.assertEqual(notif1.pk, notif2.pk)
        self.assertEqual(
            Notification.objects.filter(
                organization=self.org_a, idempotency_key=idemp_key
            ).count(),
            1,
        )

    def test_idempotency_cancellation_distinct_from_confirmation(self):
        confirm_key = "booking:100:confirmed:student_a1"
        cancel_key = "booking:100:cancelled:student_a1"

        notif_confirm = create_notification(
            organization=self.org_a,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=self.student_a1,
            title="Booking Confirmed",
            idempotency_key=confirm_key,
        )
        notif_cancel = create_notification(
            organization=self.org_a,
            event_type=EventType.BOOKING_CANCELLED,
            recipient=self.student_a1,
            title="Booking Cancelled",
            idempotency_key=cancel_key,
        )

        self.assertNotEqual(notif_confirm.pk, notif_cancel.pk)
        self.assertEqual(notif_confirm.event_type, EventType.BOOKING_CONFIRMED)
        self.assertEqual(notif_cancel.event_type, EventType.BOOKING_CANCELLED)

    # --- 6. Provider Failure Isolation ---

    def test_provider_failure_auditable_notification_retained(self):
        notif = create_notification(
            organization=self.org_a,
            event_type=EventType.BOOKING_CONFIRMED,
            recipient=self.student_a1,
            title="Delivery Failure Test",
        )

        # In-app delivery succeeds
        deliv_inapp = deliver_notification(notif, DeliveryChannel.IN_APP)
        self.assertEqual(deliv_inapp.status, DeliveryStatus.SENT)

        # Email delivery fails
        failing_provider = MockFailingProvider(
            error_code="SMTP_AUTH_FAILED",
            error_message="Authentication refused by mail gateway",
        )
        deliv_email = deliver_notification(
            notif, DeliveryChannel.EMAIL, provider=failing_provider
        )

        self.assertEqual(deliv_email.status, DeliveryStatus.FAILED)
        self.assertEqual(deliv_email.error_code, "SMTP_AUTH_FAILED")
        self.assertIn("Authentication refused", deliv_email.error_message)

        # Invariants:
        notif.refresh_from_db()
        self.assertEqual(notif.organization, self.org_a)
        self.assertEqual(notif.deliveries.count(), 2)

        deliv_inapp.refresh_from_db()
        self.assertEqual(deliv_inapp.status, DeliveryStatus.SENT)

    # --- 7. Privacy Leak Prevention ---

    def test_privacy_leak_prevention_rejects_qc_notes_and_tokens(self):
        leaked_payloads = [
            {"flag_reason": "Teacher noted poor student attendance"},
            {"lead_review_note": "Confidential QA note for teacher"},
            {"qc_notes": "Internal evaluation only"},
            {"password": "plain-text-password"},
            {"token": "auth-bearer-token"},
            {"access_token": "oauth-token"},
            {"rate_used": "50.00"},
            {"nested": {"secret": "super_secret_key"}},
        ]
        for payload in leaked_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    create_notification(
                        organization=self.org_a,
                        event_type=EventType.PROGRESS_READY,
                        recipient=self.student_a1,
                        title="Leaked Payload Test",
                        payload=payload,
                    )
