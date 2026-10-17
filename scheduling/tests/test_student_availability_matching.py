from datetime import time
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import ParentLink, Role, User
from curriculum.models import Level, TeacherTrack, Track
from notifications.models import EventType, Notification
from organizations.models import (
    EnrollmentStatus,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    ScheduleAgreementStatus,
    StudentEnrollment,
)
from scheduling.models import Availability, StudentAvailability, Weekday


class StudentAvailabilityAndMatchingTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # 1. Create Organization
        self.org = Organization.objects.create(name="Al-Hikmah Academy", slug="al-hikmah", timezone="UTC")

        # 2. Create Owner
        self.owner = User.objects.create_user(
            username="owner_zayd", email="zayd@academy.com", role=Role.OWNER
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.owner,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )

        # 3. Create Teachers
        from accounts.models import OrganizationTeacherConfiguration

        self.teacher1 = User.objects.create_user(
            username="teacher_ahmad", email="ahmad@academy.com", role=Role.SUB
        )
        self.teacher1_membership = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.teacher1,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.ACTIVE,
        )
        OrganizationTeacherConfiguration.objects.create(
            membership=self.teacher1_membership,
            approved=True,
            max_weekly_hours=20,
            hourly_payout_rate=15.00,
        )

        self.teacher2 = User.objects.create_user(
            username="teacher_bilal", email="bilal@academy.com", role=Role.SUB
        )
        self.teacher2_membership = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.teacher2,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.ACTIVE,
        )
        OrganizationTeacherConfiguration.objects.create(
            membership=self.teacher2_membership,
            approved=True,
            max_weekly_hours=20,
            hourly_payout_rate=15.00,
        )

        # 4. Create Track and Level
        self.track = Track.objects.create(
            organization=self.org,
            name="Quran Recitation",
            slug="quran-recitation",
        )
        self.level = Level.objects.create(
            track=self.track,
            name="Level 1: Beginners",
            order=1,
        )

        # Authorize teacher 1 and teacher 2 for track
        TeacherTrack.objects.create(
            membership=self.teacher1_membership,
            track=self.track,
            active=True,
        )
        TeacherTrack.objects.create(
            membership=self.teacher2_membership,
            track=self.track,
            active=True,
        )

        # Teacher 1 availability: Mon & Wed 16:00 - 18:00 UTC
        Availability.objects.create(
            organization=self.org,
            teacher=self.teacher1,
            weekday=Weekday.MONDAY,
            start_time_utc=time(16, 0),
            end_time_utc=time(18, 0),
        )
        Availability.objects.create(
            organization=self.org,
            teacher=self.teacher1,
            weekday=Weekday.WEDNESDAY,
            start_time_utc=time(16, 0),
            end_time_utc=time(18, 0),
        )

        # Teacher 2 availability: Tue & Thu 10:00 - 12:00 UTC (No overlap with evening)
        Availability.objects.create(
            organization=self.org,
            teacher=self.teacher2,
            weekday=Weekday.TUESDAY,
            start_time_utc=time(10, 0),
            end_time_utc=time(12, 0),
        )

        # 5. Create Parent and Minor Student
        self.parent = User.objects.create_user(
            username="parent_fatima", email="fatima@family.com", role=Role.PARENT
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.parent,
            role=OrganizationRole.PARENT,
            status=MembershipStatus.ACTIVE,
        )

        self.student = User.objects.create_user(
            username="student_yusuf", email="yusuf@family.com", role=Role.STUDENT
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.student,
            role=OrganizationRole.STUDENT,
            status=MembershipStatus.ACTIVE,
        )
        ParentLink.objects.create(parent=self.parent, student=self.student)

        # Create active StudentEnrollment with teacher1 allocated
        self.enrollment = StudentEnrollment.objects.create(
            organization=self.org,
            user=self.student,
            track=self.track,
            level=self.level,
            teacher=self.teacher1,
            status=EnrollmentStatus.ACTIVE,
            schedule_agreement_status=ScheduleAgreementStatus.AGREED,
        )

    def test_student_declares_availability(self):
        """Student can set their own weekly availability windows."""
        self.client.force_authenticate(user=self.student)
        url = reverse("scheduling:student-availability-list", kwargs={"organization_pk": self.org.pk})

        payload = {
            "windows": [
                {"weekday": Weekday.MONDAY, "start_time": "16:00:00", "end_time": "18:00:00"},
                {"weekday": Weekday.WEDNESDAY, "start_time": "16:00:00", "end_time": "18:00:00"},
            ]
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(StudentAvailability.objects.filter(student=self.student).count(), 2)

    def test_parent_declares_availability_for_child(self):
        """Parent of a minor student can set availability on behalf of child."""
        self.client.force_authenticate(user=self.parent)
        url = reverse("scheduling:student-availability-list", kwargs={"organization_pk": self.org.pk})

        payload = {
            "student_id": self.student.id,
            "windows": [
                {"weekday": Weekday.MONDAY, "start_time": "17:00:00", "end_time": "19:00:00"},
            ],
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(StudentAvailability.objects.filter(student=self.student).count(), 1)
        avail = StudentAvailability.objects.first()
        self.assertEqual(avail.created_by, self.parent)

    def test_unrelated_user_cannot_set_student_availability(self):
        """Unrelated user is denied from setting another student's availability."""
        unrelated = User.objects.create_user(
            username="other_student", email="other@test.com", role=Role.STUDENT
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=unrelated,
            role=OrganizationRole.STUDENT,
            status=MembershipStatus.ACTIVE,
        )
        self.client.force_authenticate(user=unrelated)
        url = reverse("scheduling:student-availability-list", kwargs={"organization_pk": self.org.pk})

        payload = {
            "student_id": self.student.id,
            "windows": [
                {"weekday": Weekday.FRIDAY, "start_time": "10:00:00", "end_time": "11:00:00"},
            ],
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_academy_owner_cannot_declare_student_availability(self):
        """Academy owner is denied from setting student availability (reserved solely for student or parent)."""
        self.client.force_authenticate(user=self.owner)
        url = reverse("scheduling:student-availability-list", kwargs={"organization_pk": self.org.pk})

        payload = {
            "student_id": self.student.id,
            "windows": [
                {"weekday": Weekday.FRIDAY, "start_time": "10:00:00", "end_time": "11:00:00"},
            ],
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("Student availability can only be declared", str(response.data))

    def test_timing_adjustment_resets_agreement_and_notifies_teacher(self):
        """Updating availability resets enrollment agreement to pending and sends notification to allocated teacher."""
        self.client.force_authenticate(user=self.parent)
        url = reverse("scheduling:student-availability-list", kwargs={"organization_pk": self.org.pk})

        payload = {
            "student_id": self.student.id,
            "windows": [
                {"weekday": Weekday.MONDAY, "start_time": "16:30:00", "end_time": "18:00:00"},
            ],
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Check enrollment status reset
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.schedule_agreement_status, ScheduleAgreementStatus.PENDING)

        # Check notification delivered to allocated teacher1
        notif = Notification.objects.filter(
            organization=self.org,
            recipient=self.teacher1,
            event_type=EventType.STUDENT_AVAILABILITY_UPDATED,
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Schedule Adjusted", notif.title)
        self.assertEqual(notif.payload["student_id"], self.student.id)

    def test_allocated_teacher_can_agree_to_schedule(self):
        """Allocated teacher can agree to current student availability."""
        self.client.force_authenticate(user=self.teacher1)
        url = reverse(
            "scheduling:student-availability-agree",
            kwargs={"organization_pk": self.org.pk, "student_id": self.student.pk},
        )

        response = self.client.post(url, {"agreed": True, "notes": "Looks perfect!"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.schedule_agreement_status, ScheduleAgreementStatus.AGREED)
        self.assertEqual(self.enrollment.schedule_agreement_notes, "Looks perfect!")
        self.assertIsNotNone(self.enrollment.schedule_agreed_at)

        # Student & parent receive confirmation notification
        self.assertTrue(
            Notification.objects.filter(
                organization=self.org,
                recipient=self.student,
                event_type=EventType.SCHEDULE_AGREEMENT_RESPONSE,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                organization=self.org,
                recipient=self.parent,
                event_type=EventType.SCHEDULE_AGREEMENT_RESPONSE,
            ).exists()
        )

    def test_allocated_teacher_can_propose_alternative(self):
        """Allocated teacher can decline current timing and propose alternative."""
        self.client.force_authenticate(user=self.teacher1)
        url = reverse(
            "scheduling:student-availability-agree",
            kwargs={"organization_pk": self.org.pk, "student_id": self.student.pk},
        )

        response = self.client.post(
            url,
            {"agreed": False, "notes": "I have another class at 16:30. Can we do 17:00?"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.enrollment.refresh_from_db()
        self.assertEqual(
            self.enrollment.schedule_agreement_status, ScheduleAgreementStatus.ALTERNATIVE_PROPOSED
        )
        self.assertEqual(
            self.enrollment.schedule_agreement_notes,
            "I have another class at 16:30. Can we do 17:00?",
        )

    def test_matching_teachers_calculates_overlap_cleanly(self):
        """Owner matching-teachers endpoint compares student and teacher hours and ranks best match first."""
        # Set student availability: Mon 16:00-18:00 and Wed 16:00-18:00
        StudentAvailability.objects.create(
            organization=self.org,
            student=self.student,
            weekday=Weekday.MONDAY,
            start_time_utc=time(16, 0),
            end_time_utc=time(18, 0),
        )
        StudentAvailability.objects.create(
            organization=self.org,
            student=self.student,
            weekday=Weekday.WEDNESDAY,
            start_time_utc=time(16, 0),
            end_time_utc=time(18, 0),
        )

        self.client.force_authenticate(user=self.owner)
        url = reverse("scheduling:matching-teachers-list", kwargs={"organization_pk": self.org.pk})
        response = self.client.get(f"{url}?student_id={self.student.id}&track_id={self.track.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Teacher 1 should have high match (240 min = 4 hours overlap)
        # Teacher 2 should have none match (0 overlap)
        matches = {m["teacher_id"]: m for m in response.data}
        self.assertIn(self.teacher1.id, matches)
        self.assertIn(self.teacher2.id, matches)

        t1_match = matches[self.teacher1.id]
        self.assertEqual(t1_match["match_quality"], "high")
        self.assertEqual(t1_match["overlap_total_minutes"], 240)
        self.assertEqual(t1_match["overlap_total_hours"], 4.0)
        self.assertEqual(len(t1_match["overlapping_windows"]), 2)
        self.assertIn("start_time", t1_match["overlapping_windows"][0])
        self.assertIn("end_time", t1_match["overlapping_windows"][0])
        self.assertEqual(t1_match["overlapping_windows"][0]["start_time"], "16:00:00")
        self.assertEqual(t1_match["overlapping_windows"][0]["end_time"], "18:00:00")

        t2_match = matches[self.teacher2.id]
        self.assertEqual(t2_match["match_quality"], "none")
        self.assertEqual(t2_match["overlap_total_minutes"], 0)
