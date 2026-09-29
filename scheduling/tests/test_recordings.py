"""Tests for ClassSessionRecording, owner access control, and 60-day auto-retention policy."""

from datetime import time, timedelta
from io import StringIO

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role
from accounts.tests.factories import (
    ParentFactory,
    StudentFactory,
    UserFactory,
)
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from organizations.models import OrganizationMembership, OrganizationRole
from organizations.tests.factories import OrganizationFactory
from scheduling.models import Booking, BookingStatus, ClassSessionRecording, RecordingStatus, Weekday
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableTeacherFactory,
    BookingFactory,
    slot_at,
    teaches,
)


class SessionRecordingAPITests(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.other_org = OrganizationFactory()

        self.owner = UserFactory()
        self.membership_owner = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.owner,
            role=OrganizationRole.OWNER,
        )

        self.teacher = BookableTeacherFactory()
        self.membership_teacher = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.teacher,
            role=OrganizationRole.TEACHER,
        )

        self.window = AvailabilityFactory(
            organization=self.org,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        self.student = admit(StudentFactory(), self.org).user

        self.track = TrackFactory(organization=self.org)
        self.level = LevelFactory(track=self.track)
        teaches(self.teacher, self.level)

        self.booking = BookingFactory(
            availability=self.window,
            student=self.student,
            level=self.level,
            start_time_utc=slot_at(self.window),
            status=BookingStatus.SCHEDULED,
            video_provider="jitsi",
            video_provider_meeting_id="darul-quran-room-123",
            video_join_url="https://meet.jit.si/darul-quran-room-123",
        )

        self.list_url = reverse(
            "scheduling:recording-list",
            kwargs={"organization_pk": self.org.pk},
        )

    def test_booking_completion_creates_session_recording_with_60_day_retention(self):
        self.client.force_authenticate(user=self.teacher)
        complete_url = reverse(
            "scheduling:booking-complete",
            kwargs={"organization_pk": self.org.pk, "pk": self.booking.pk},
        )
        response = self.client.post(complete_url, {"duration_minutes": 45}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.COMPLETED)
        self.assertEqual(self.booking.duration_minutes, 45)

        recording = ClassSessionRecording.objects.filter(booking=self.booking).first()
        self.assertIsNotNone(recording)
        self.assertEqual(recording.organization, self.org)
        self.assertEqual(recording.duration_minutes, 45)
        self.assertEqual(recording.video_room_name, "darul-quran-room-123")
        self.assertEqual(recording.recording_url, "https://meet.jit.si/darul-quran-room-123")
        self.assertEqual(recording.status, RecordingStatus.READY)

        # Retention check: expires_at is approximately 60 days in future
        now = dj_timezone.now()
        expected_expiry = now + timedelta(days=60)
        self.assertAlmostEqual(
            recording.expires_at.timestamp(),
            expected_expiry.timestamp(),
            delta=60,
        )
        self.assertFalse(recording.is_expired)
        self.assertGreaterEqual(recording.days_until_expiry, 59)

    def test_owner_can_list_and_view_recordings(self):
        # Create active recording
        recording = ClassSessionRecording.objects.create(
            booking=self.booking,
            organization=self.org,
            title="Level 1 Tajweed with Ustadh Ahmad",
            duration_minutes=30,
            recorded_at=dj_timezone.now(),
            expires_at=dj_timezone.now() + timedelta(days=60),
            status=RecordingStatus.READY,
        )

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], recording.id)
        self.assertEqual(response.data[0]["title"], "Level 1 Tajweed with Ustadh Ahmad")
        self.assertIn("days_until_expiry", response.data[0])
        self.assertFalse(response.data[0]["is_expired"])
        self.assertEqual(response.data[0]["student"]["id"], self.student.id)
        self.assertEqual(response.data[0]["teacher"]["id"], self.teacher.id)

    def test_student_and_teacher_cannot_access_owner_recordings(self):
        ClassSessionRecording.objects.create(
            booking=self.booking,
            organization=self.org,
            duration_minutes=30,
            recorded_at=dj_timezone.now(),
            expires_at=dj_timezone.now() + timedelta(days=60),
        )

        # Student attempt -> 403 Forbidden
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Teacher attempt -> 403 Forbidden
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_manually_delete_recording(self):
        recording = ClassSessionRecording.objects.create(
            booking=self.booking,
            organization=self.org,
            duration_minutes=30,
            recorded_at=dj_timezone.now(),
            expires_at=dj_timezone.now() + timedelta(days=60),
        )

        detail_url = reverse(
            "scheduling:recording-detail",
            kwargs={"organization_pk": self.org.pk, "pk": recording.pk},
        )

        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ClassSessionRecording.objects.filter(pk=recording.pk).exists())

    def test_purge_expired_recordings_command_deletes_older_than_60_days(self):
        # Active recording (expires in 50 days)
        active_rec = ClassSessionRecording.objects.create(
            booking=self.booking,
            organization=self.org,
            title="Active Recording",
            duration_minutes=30,
            recorded_at=dj_timezone.now() - timedelta(days=10),
            expires_at=dj_timezone.now() + timedelta(days=50),
        )

        # Expired recording (created 65 days ago, expired 5 days ago)
        expired_rec = ClassSessionRecording.objects.create(
            booking=self.booking,
            organization=self.org,
            title="Expired Recording",
            duration_minutes=30,
            recorded_at=dj_timezone.now() - timedelta(days=65),
            expires_at=dj_timezone.now() - timedelta(days=5),
        )

        out = StringIO()
        call_command("purge_expired_recordings", stdout=out)

        # Expired recording is deleted from database
        self.assertFalse(ClassSessionRecording.objects.filter(pk=expired_rec.pk).exists())
        # Active recording remains intact
        self.assertTrue(ClassSessionRecording.objects.filter(pk=active_rec.pk).exists())
        self.assertIn("Successfully purged 1 expired class session recordings", out.getvalue())
