from datetime import timedelta
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import StudentFactory
from audit_logs.models import AuditAction, AuditLog
from curriculum.tests.factories import LevelFactory
from organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory
from scheduling.models import Booking, BookingStatus, Cohort, RoutedReason
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableLeadTeacherFactory,
    BookableTeacherFactory,
    CohortFactory,
    ensure_teacher_configured,
    slot_at,
)


class CohortLifecycleAPITests(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.lead = BookableLeadTeacherFactory()
        self.lead_m = OrganizationMembershipFactory(user=self.lead, organization=self.org, role="admin")

        self.teacher = BookableTeacherFactory()
        self.teacher_m = OrganizationMembershipFactory(user=self.teacher, organization=self.org, role="teacher")

        self.student = StudentFactory()
        self.student_m = OrganizationMembershipFactory(user=self.student, organization=self.org, role="student")

        self.level = LevelFactory(track__organization=self.org, group_eligible=True)
        from curriculum.tests.factories import TeacherTrackFactory
        TeacherTrackFactory(membership=self.teacher_m, track=self.level.track)
        TeacherTrackFactory(membership=self.lead_m, track=self.level.track)

        ensure_teacher_configured(self.teacher, self.org)
        ensure_teacher_configured(self.lead, self.org)

        self.window = AvailabilityFactory(teacher=self.teacher, organization=self.org)
        self.slot = slot_at(self.window)

        self.client.force_authenticate(user=self.lead)

    def test_create_cohort_logs_audit(self):
        url = f"/api/scheduling/organizations/{self.org.pk}/cohorts/"
        payload = {
            "teacher": self.teacher.pk,
            "level": self.level.pk,
            "max_students": 5,
            "schedule_start_utc": self.slot.isoformat(),
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        cohort_id = res.data["id"]
        self.assertEqual(res.data["max_students"], 5)

        # Audit log verified
        log = AuditLog.objects.filter(
            organization=self.org,
            action=AuditAction.COHORT_CREATED,
            object_id=str(cohort_id),
        ).first()
        self.assertIsNotNone(log)

    def test_update_cohort(self):
        cohort = CohortFactory(
            level=self.level,
            teacher=self.teacher,
            schedule_start_utc=self.slot,
            max_students=4,
        )
        url = f"/api/scheduling/organizations/{self.org.pk}/cohorts/{cohort.pk}/"
        res = self.client.patch(url, {"max_students": 8}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        cohort.refresh_from_db()
        self.assertEqual(cohort.max_students, 8)

        log = AuditLog.objects.filter(
            organization=self.org,
            action=AuditAction.COHORT_UPDATED,
            object_id=str(cohort.pk),
        ).first()
        self.assertIsNotNone(log)

    def test_cancel_cohort_and_bookings(self):
        cohort = CohortFactory(
            level=self.level,
            teacher=self.teacher,
            schedule_start_utc=self.slot,
            max_students=4,
        )
        cohort.add_student(self.student)
        booking = Booking.objects.create(
            student=self.student,
            teacher=self.teacher,
            level=self.level,
            start_time_utc=self.slot,
            cohort=cohort,
            status=BookingStatus.SCHEDULED,
        )

        url = f"/api/scheduling/organizations/{self.org.pk}/cohorts/{cohort.pk}/"
        res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        # Booking cancelled
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)

        log = AuditLog.objects.filter(
            organization=self.org,
            action=AuditAction.COHORT_CANCELLED,
            object_id=str(cohort.pk),
        ).first()
        self.assertIsNotNone(log)

    def test_add_and_remove_student_from_cohort(self):
        cohort = CohortFactory(
            level=self.level,
            teacher=self.teacher,
            schedule_start_utc=self.slot,
            max_students=2,
        )

        add_url = f"/api/scheduling/organizations/{self.org.pk}/cohorts/{cohort.pk}/students/"
        res = self.client.post(add_url, {"student_id": self.student.pk}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn(self.student, cohort.students.all())
        self.assertEqual(res.data["seats_taken"], 1)

        # Remove student
        remove_url = f"/api/scheduling/organizations/{self.org.pk}/cohorts/{cohort.pk}/students/{self.student.pk}/"
        res_del = self.client.delete(remove_url)
        self.assertEqual(res_del.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.student, cohort.students.all())
        self.assertEqual(res_del.data["seats_taken"], 0)

    def test_student_self_enrollment(self):
        cohort = CohortFactory(
            level=self.level,
            teacher=self.teacher,
            schedule_start_utc=self.slot,
            max_students=3,
        )

        self.client.force_authenticate(user=self.student)
        enroll_url = f"/api/scheduling/organizations/{self.org.pk}/cohorts/{cohort.pk}/enroll/"
        res = self.client.post(enroll_url, {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn(self.student, cohort.students.all())

        # Verify booking created with cohort link
        booking = Booking.objects.filter(student=self.student, cohort=cohort).first()
        self.assertIsNotNone(booking)
        self.assertEqual(booking.routed_reason, RoutedReason.COHORT_ASSIGNED)
