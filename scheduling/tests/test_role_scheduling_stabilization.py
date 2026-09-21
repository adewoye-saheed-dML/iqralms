"""Tests for scheduling role scoping and meeting access stabilization (Sections 4 & 12 of IQRA_BE_ROLE_ONBOARDING_CLASS_STABILIZATION.md)."""

from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import ParentLink, Role
from accounts.tests.factories import ParentFactory, StudentFactory, SubTeacherFactory, UserFactory
from curriculum.tests.factories import LevelFactory, TrackFactory
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
from organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory
from scheduling.models import Booking, BookingStatus, Cohort


class RoleSchedulingStabilizationTests(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.other_org = OrganizationFactory()

        self.owner = UserFactory(role=Role.LEAD)
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.owner,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )

        self.admin_user = UserFactory(role=Role.LEAD)
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.admin_user,
            role=OrganizationRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )

        self.lead_teacher = UserFactory(role=Role.LEAD)
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.lead_teacher,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.ACTIVE,
        )

        self.sub_teacher = SubTeacherFactory()
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.sub_teacher,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.ACTIVE,
        )

        self.other_teacher = SubTeacherFactory()
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.other_teacher,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.ACTIVE,
        )

        self.parent = ParentFactory()
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.parent,
            role=OrganizationRole.PARENT,
            status=MembershipStatus.ACTIVE,
        )

        self.student = StudentFactory()
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.student,
            role=OrganizationRole.STUDENT,
            status=MembershipStatus.ACTIVE,
        )
        ParentLink.objects.create(parent=self.parent, student=self.student)

        self.track = TrackFactory(organization=self.org)
        self.level = LevelFactory(track=self.track)

        # Create a booking directly with bulk_create to isolate endpoint test logic
        self.booking = Booking.objects.bulk_create([
            Booking(
                student=self.student,
                teacher=self.sub_teacher,
                level=self.level,
                start_time_utc=timezone.now() + timedelta(days=2),
                duration_minutes=30,
                status=BookingStatus.SCHEDULED,
                video_provider="jitsi",
                video_provider_meeting_id="iqra-test-meeting-123",
                video_join_url="https://meet.jit.si/iqra-test-meeting-123",
            )
        ])[0]

    # --- Section 4: Academy Bookings (/bookings/academy/) ---

    def test_academy_bookings_role_permissions(self):
        url = reverse("scheduling:booking-academy", args=[self.org.pk])

        # 1. Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 1)

        # 2. Admin allowed
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 3. Lead teacher allowed
        self.client.force_authenticate(user=self.lead_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 4. Sub-teacher denied
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Student denied
        self.client.force_authenticate(user=self.student)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # 6. Parent denied
        self.client.force_authenticate(user=self.parent)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_academy_bookings_filters(self):
        url = reverse("scheduling:booking-academy", args=[self.org.pk])
        self.client.force_authenticate(user=self.owner)

        # Filter by correct teacher
        res = self.client.get(url, {"teacher_id": self.sub_teacher.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 1)

        # Filter by different teacher
        res = self.client.get(url, {"teacher_id": self.other_teacher.id})
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 0)

        # Filter by track
        res = self.client.get(url, {"track_id": self.track.id})
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 1)

        # Filter by status
        res = self.client.get(url, {"status": "scheduled"})
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 1)

        res = self.client.get(url, {"status": "cancelled"})
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 0)

    def test_academy_bookings_cross_tenant(self):
        other_owner = UserFactory(role=Role.LEAD)
        OrganizationMembershipFactory(
            organization=self.other_org,
            user=other_owner,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        url = reverse("scheduling:booking-academy", args=[self.org.pk])
        self.client.force_authenticate(user=other_owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # --- Section 4: Cohort Management Permissions ---

    def test_cohort_list_and_create_permissions(self):
        url = reverse("scheduling:cohort-create", args=[self.org.pk])

        # Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Lead Teacher allowed
        self.client.force_authenticate(user=self.lead_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Sub-teacher denied
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # --- Section 4: Waitlist Permissions ---

    def test_teacher_waitlist_permissions(self):
        url = reverse("scheduling:waitlist-for-teacher", args=[self.org.pk])

        # Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url, {"teacher_id": self.sub_teacher.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Sub-teacher denied
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url, {"teacher_id": self.sub_teacher.id})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # --- Section 12: Provider-neutral Class Meeting Access (/bookings/{pk}/meeting/) ---

    def test_meeting_access_assigned_teacher(self):
        url = reverse("scheduling:booking-meeting", args=[self.org.pk, self.booking.pk])
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["provider"], "jitsi")
        self.assertEqual(res.data["provider_meeting_id"], "iqra-test-meeting-123")
        self.assertEqual(res.data["join_url"], "https://meet.jit.si/iqra-test-meeting-123")

    def test_meeting_access_booked_student(self):
        url = reverse("scheduling:booking-meeting", args=[self.org.pk, self.booking.pk])
        self.client.force_authenticate(user=self.student)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["join_url"], "https://meet.jit.si/iqra-test-meeting-123")

    def test_meeting_access_parent_of_student(self):
        url = reverse("scheduling:booking-meeting", args=[self.org.pk, self.booking.pk])
        self.client.force_authenticate(user=self.parent)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["join_url"], "https://meet.jit.si/iqra-test-meeting-123")

    def test_meeting_access_owner_and_admin(self):
        url = reverse("scheduling:booking-meeting", args=[self.org.pk, self.booking.pk])

        # Owner
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Admin
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_meeting_access_unrelated_user_denied(self):
        url = reverse("scheduling:booking-meeting", args=[self.org.pk, self.booking.pk])
        self.client.force_authenticate(user=self.other_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("do not have access", str(res.data).lower())

    def test_meeting_access_cancelled_booking_rejected(self):
        Booking.objects.filter(pk=self.booking.pk).update(status=BookingStatus.CANCELLED)
        self.booking.refresh_from_db()

        url = reverse("scheduling:booking-meeting", args=[self.org.pk, self.booking.pk])
        self.client.force_authenticate(user=self.student)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cancelled", str(res.data).lower())

    def test_meeting_access_cross_tenant_rejected(self):
        url = reverse("scheduling:booking-meeting", args=[self.other_org.pk, self.booking.pk])
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        # Not in other_org -> forbidden by tenant check or 404
        self.assertIn(res.status_code, {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND})
