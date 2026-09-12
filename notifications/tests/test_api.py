"""API endpoint tests for organization-scoped notifications and deliveries."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import UserFactory
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
from organizations.tests.factories import OrganizationFactory

from notifications.models import DeliveryChannel, EventType, Notification, NotificationDelivery
from notifications.tests.factories import NotificationDeliveryFactory, NotificationFactory


class NotificationAPITests(APITestCase):
    def setUp(self):
        self.org_a = OrganizationFactory(name="Academy A")
        self.org_b = OrganizationFactory(name="Academy B")

        self.owner_a = UserFactory(username="owner_a")
        self.admin_a = UserFactory(username="admin_a")
        self.student_a1 = UserFactory(username="student_a1")
        self.student_a2 = UserFactory(username="student_a2")
        self.suspended_a = UserFactory(username="suspended_a")

        self.owner_b = UserFactory(username="owner_b")
        self.student_b = UserFactory(username="student_b")

        self.outsider = UserFactory(username="outsider")

        # Memberships for Org A
        OrganizationMembership.objects.create(
            organization=self.org_a,
            user=self.owner_a,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            organization=self.org_a,
            user=self.admin_a,
            role=OrganizationRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            organization=self.org_a,
            user=self.student_a1,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            organization=self.org_a,
            user=self.student_a2,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            organization=self.org_a,
            user=self.suspended_a,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.SUSPENDED,
        )

        # Memberships for Org B
        OrganizationMembership.objects.create(
            organization=self.org_b,
            user=self.owner_b,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            organization=self.org_b,
            user=self.student_b,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.ACTIVE,
        )

        # Create notifications
        self.notif_a1 = NotificationFactory(
            organization=self.org_a,
            recipient=self.student_a1,
            title="Session for A1",
            summary="Detail A1",
        )
        self.notif_a2 = NotificationFactory(
            organization=self.org_a,
            recipient=self.student_a2,
            title="Session for A2",
            summary="Detail A2",
        )
        self.notif_b = NotificationFactory(
            organization=self.org_b,
            recipient=self.student_b,
            title="Session for B",
            summary="Detail B",
        )

        # Create deliveries
        self.deliv_a1 = NotificationDeliveryFactory(
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

    # --- Mine endpoint tests ---

    def test_mine_endpoint_returns_only_callers_notifications_in_org(self):
        self.client.force_authenticate(user=self.student_a1)
        url = reverse("notifications:notification-mine", kwargs={"organization_pk": self.org_a.pk})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self._get_results(resp)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.notif_a1.pk)
        self.assertEqual(results[0]["title"], "Session for A1")

    def test_mine_endpoint_with_unread_filter(self):
        self.client.force_authenticate(user=self.student_a1)
        url = reverse("notifications:notification-mine", kwargs={"organization_pk": self.org_a.pk})
        resp = self.client.get(f"{url}?unread=true")
        self.assertEqual(len(self._get_results(resp)), 1)

        self.notif_a1.mark_as_read()
        resp = self.client.get(f"{url}?unread=true")
        self.assertEqual(len(self._get_results(resp)), 0)

    def test_mine_endpoint_denied_for_non_member(self):
        self.client.force_authenticate(user=self.outsider)
        url = reverse("notifications:notification-mine", kwargs={"organization_pk": self.org_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_mine_endpoint_denied_for_suspended_member(self):
        self.client.force_authenticate(user=self.suspended_a)
        url = reverse("notifications:notification-mine", kwargs={"organization_pk": self.org_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # --- Admin endpoint tests ---

    def test_admin_endpoint_allowed_for_owner_and_admin(self):
        for user in [self.owner_a, self.admin_a]:
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                url = reverse("notifications:notification-admin-list", kwargs={"organization_pk": self.org_a.pk})
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
                results = self._get_results(resp)
                # Sees both notif_a1 and notif_a2
                ids = [item["id"] for item in results]
                self.assertIn(self.notif_a1.pk, ids)
                self.assertIn(self.notif_a2.pk, ids)
                # Never sees notif_b
                self.assertNotIn(self.notif_b.pk, ids)

    def test_admin_endpoint_denied_for_regular_member(self):
        self.client.force_authenticate(user=self.student_a1)
        url = reverse("notifications:notification-admin-list", kwargs={"organization_pk": self.org_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # --- Detail endpoint tests ---

    def test_detail_endpoint_accessible_by_recipient(self):
        self.client.force_authenticate(user=self.student_a1)
        url = reverse(
            "notifications:notification-detail",
            kwargs={"organization_pk": self.org_a.pk, "pk": self.notif_a1.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["id"], self.notif_a1.pk)

    def test_detail_endpoint_returns_404_for_other_user_notification(self):
        # student_a2 tries to access student_a1's notification
        self.client.force_authenticate(user=self.student_a2)
        url = reverse(
            "notifications:notification-detail",
            kwargs={"organization_pk": self.org_a.pk, "pk": self.notif_a1.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_endpoint_returns_404_for_other_academy_notification(self):
        # owner_a tries to access notif_b using org_a URL
        self.client.force_authenticate(user=self.owner_a)
        url = reverse(
            "notifications:notification-detail",
            kwargs={"organization_pk": self.org_a.pk, "pk": self.notif_b.pk},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- Mark read endpoint tests ---

    def test_mark_read_endpoint_marks_notification_as_read(self):
        self.client.force_authenticate(user=self.student_a1)
        self.assertFalse(self.notif_a1.is_read)

        url = reverse(
            "notifications:notification-read",
            kwargs={"organization_pk": self.org_a.pk, "pk": self.notif_a1.pk},
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["is_read"])
        self.assertIsNotNone(resp.data["read_at"])

        self.notif_a1.refresh_from_db()
        self.assertTrue(self.notif_a1.is_read)

    def test_mark_read_endpoint_denied_for_other_user(self):
        self.client.force_authenticate(user=self.student_a2)
        url = reverse(
            "notifications:notification-read",
            kwargs={"organization_pk": self.org_a.pk, "pk": self.notif_a1.pk},
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- Deliveries endpoint tests ---

    def test_deliveries_endpoint_allowed_for_admin_only(self):
        self.client.force_authenticate(user=self.admin_a)
        url = reverse("notifications:delivery-admin-list", kwargs={"organization_pk": self.org_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = self._get_results(resp)
        ids = [item["id"] for item in results]
        self.assertIn(self.deliv_a1.pk, ids)
        self.assertNotIn(self.deliv_b.pk, ids)

    def test_deliveries_endpoint_denied_for_regular_member(self):
        self.client.force_authenticate(user=self.student_a1)
        url = reverse("notifications:delivery-admin-list", kwargs={"organization_pk": self.org_a.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
