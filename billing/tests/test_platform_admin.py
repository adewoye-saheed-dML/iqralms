"""Tests for Platform Creator / Super Admin Command Center (Phase 9/Creator Dashboard)."""

from datetime import timedelta
from django.core import mail
from django.test import TestCase
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Role, User
from audit_logs.models import AuditAction, AuditLog
from billing.models import (
    PlatformAnnouncement,
    PlatformSubscription,
    SubscriberOnboardingDispatch,
    SubscriptionStatus,
    SubscriptionTier,
)
from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    StudentEnrollment,
)


class PlatformAdminTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # 1. Superuser / Creator Admin
        self.superadmin = User.objects.create_superuser(
            username="platform_creator",
            email="creator@iqralms.com",
            password="StrongPassword123!",
            role="owner",
            timezone="UTC",
        )

        # 2. Regular academy owner (not superuser)
        self.regular_owner = User.objects.create_user(
            username="regular_owner",
            email="owner@academy.com",
            password="StrongPassword123!",
            role=Role.OWNER,
            timezone="UTC",
        )

        # 3. Regular student
        self.student_user = User.objects.create_user(
            username="student_user",
            email="student@academy.com",
            password="StrongPassword123!",
            role=Role.STUDENT,
            timezone="UTC",
        )

        # 4. Academy 1 with Active Subscription
        self.org1 = Organization.objects.create(
            name="Al-Noor Academy",
            slug="al-noor",
            timezone="UTC",
            currency="USD",
            is_active=True,
        )
        OrganizationMembership.objects.create(
            organization=self.org1,
            user=self.regular_owner,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        self.sub1 = PlatformSubscription.objects.create(
            organization=self.org1,
            tier=SubscriptionTier.GROWTH,
            status=SubscriptionStatus.ACTIVE,
            current_period_end=dj_timezone.now() + timedelta(days=20),
        )

        # Add student enrollment to org1
        StudentEnrollment.objects.create(
            organization=self.org1,
            user=self.student_user,
            status="active",
        )

        # 5. Academy 2 with Pending Subscription
        self.org2 = Organization.objects.create(
            name="Al-Hikmah Academy",
            slug="al-hikmah",
            timezone="UTC",
            currency="NGN",
            is_active=True,
        )
        self.sub2 = PlatformSubscription.objects.create(
            organization=self.org2,
            tier=SubscriptionTier.STARTER,
            status=SubscriptionStatus.PENDING,
        )

        # Add Audit log
        AuditLog.objects.create(
            organization=self.org1,
            actor=self.regular_owner,
            action=AuditAction.ORGANIZATION_CREATED,
            object_type="Organization",
            object_id=str(self.org1.id),
        )

    def test_unauthenticated_cannot_access_platform_admin(self):
        resp = self.client.get("/api/platform-admin/overview/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_regular_user_cannot_access_platform_admin(self):
        self.client.force_authenticate(user=self.student_user)
        resp = self.client.get("/api/platform-admin/overview/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_regular_academy_owner_without_staff_cannot_access(self):
        self.client.force_authenticate(user=self.regular_owner)
        resp = self.client.get("/api/platform-admin/overview/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_superadmin_can_retrieve_overview_kpis(self):
        self.client.force_authenticate(user=self.superadmin)
        resp = self.client.get("/api/platform-admin/overview/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()

        self.assertEqual(data["total_academies"], 2)
        self.assertEqual(data["active_subscribers"], 1)
        self.assertEqual(data["trial_subscribers"], 1)
        self.assertEqual(data["total_students"], 1)
        self.assertIn("mrr_usd", data)
        self.assertIn("system_health", data)
        self.assertEqual(data["system_health"]["database"], "operational")

    def test_superadmin_can_list_and_filter_subscribers(self):
        self.client.force_authenticate(user=self.superadmin)
        resp = self.client.get("/api/platform-admin/subscribers/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(len(data), 2)

        # Filter by status
        resp_filtered = self.client.get("/api/platform-admin/subscribers/?status=active")
        self.assertEqual(resp_filtered.status_code, status.HTTP_200_OK)
        filtered_data = resp_filtered.json()
        self.assertEqual(len(filtered_data), 1)
        self.assertEqual(filtered_data[0]["slug"], "al-noor")
        self.assertEqual(filtered_data[0]["tier"], "growth")
        self.assertEqual(filtered_data[0]["active_students_count"], 1)

    def test_superadmin_can_manage_subscriber_subscription(self):
        self.client.force_authenticate(user=self.superadmin)

        # Comp subscription (extend 365 days and activate)
        resp = self.client.post(
            f"/api/platform-admin/subscribers/{self.org2.id}/manage/",
            {"action": "comp"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.sub2.refresh_from_db()
        self.assertEqual(self.sub2.status, SubscriptionStatus.ACTIVE)
        self.assertIsNotNone(self.sub2.current_period_end)

        # Update tier to professional
        resp_tier = self.client.post(
            f"/api/platform-admin/subscribers/{self.org2.id}/manage/",
            {"action": "update_tier", "tier": "professional"},
            format="json",
        )
        self.assertEqual(resp_tier.status_code, status.HTTP_200_OK)
        self.sub2.refresh_from_db()
        self.assertEqual(self.sub2.tier, SubscriptionTier.PROFESSIONAL)

    def test_superadmin_can_view_notable_activities(self):
        self.client.force_authenticate(user=self.superadmin)
        resp = self.client.get("/api/platform-admin/activities/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertGreaterEqual(len(data), 1)
        self.assertEqual(data[0]["action"], "organization.created")
        self.assertEqual(data[0]["category"], "Academies")

    def test_superadmin_can_send_onboarding_guide_email(self):
        self.client.force_authenticate(user=self.superadmin)
        mail.outbox.clear()

        resp = self.client.post(
            "/api/platform-admin/send-onboarding-email/",
            {
                "organization_id": self.org1.id,
                "custom_message": "Welcome Ustadh! Let us know if you need any assistance getting started.",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["recipient"], "owner@academy.com")
        self.assertEqual(data["videos_included_count"], 5)

        # Verify email was sent
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertIn("owner@academy.com", sent_email.to)
        self.assertIn("Iqra LMS", sent_email.subject)
        self.assertIn("Academy Setup & Custom Branding", sent_email.body)

        # Verify database records
        self.sub1.refresh_from_db()
        self.assertIsNotNone(self.sub1.onboarding_email_sent_at)
        self.assertEqual(SubscriberOnboardingDispatch.objects.count(), 1)

    def test_superadmin_can_create_and_list_announcements(self):
        self.client.force_authenticate(user=self.superadmin)

        resp = self.client.post(
            "/api/platform-admin/announcements/",
            {
                "title": "Platform Maintenance Scheduled",
                "message": "We will be rolling out recitation video recording upgrades on Sunday at 02:00 UTC.",
                "level": "info",
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PlatformAnnouncement.objects.count(), 1)

        resp_list = self.client.get("/api/platform-admin/announcements/")
        self.assertEqual(resp_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_list.json()), 1)
