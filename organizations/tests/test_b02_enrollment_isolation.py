"""Phase B02 tests — membership vs enrollment invariants.

Proves the six things the B02 playbook requires:

1. student A enrolls in academy A
2. student A cannot be treated as enrolled in academy B
3. academy A cannot read academy B enrollment
4. track from A cannot be paired with enrollment B
5. level from A cannot be paired with enrollment B
6. suspended/inactive enrollment behavior is deterministic
"""

from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import UserFactory
from organizations.models import (
    EnrollmentStatus,
    OrganizationMembership,
    OrganizationRole,
    StudentEnrollment,
)
from organizations.tests.factories import (
    OrganizationFactory,
    OrganizationMembershipFactory,
)


class B02EnrollmentIsolationModelTests(TestCase):
    """Model-level invariants for the enrollment / membership boundary."""

    def setUp(self):
        self.academy_a = OrganizationFactory()
        self.academy_b = OrganizationFactory()
        self.student = UserFactory(role=Role.STUDENT)

    # ---- 1. student A enrolls in academy A ---------------------------------

    def test_student_enrolls_in_academy(self):
        enrollment = StudentEnrollment.objects.create(
            organization=self.academy_a,
            user=self.student,
        )
        self.assertEqual(enrollment.organization, self.academy_a)
        self.assertEqual(enrollment.user, self.student)
        self.assertTrue(enrollment.is_active)

    # ---- 2. student A cannot be treated as enrolled in academy B -----------

    def test_enrollment_scoped_to_own_academy(self):
        StudentEnrollment.objects.create(
            organization=self.academy_a,
            user=self.student,
        )
        # The student is enrolled in A, but has no enrollment in B.
        self.assertFalse(
            StudentEnrollment.objects.filter(
                organization=self.academy_b, user=self.student
            ).exists()
        )

    # ---- 3. academy A cannot read academy B enrollment ---------------------

    def test_enrollment_queryset_isolates_academies(self):
        student_b = UserFactory(role=Role.STUDENT)
        StudentEnrollment.objects.create(
            organization=self.academy_b,
            user=student_b,
        )
        # Academy A's queryset should not include academy B's enrollment.
        a_enrollments = StudentEnrollment.objects.filter(
            organization=self.academy_a
        )
        self.assertEqual(a_enrollments.count(), 0)

    # ---- 4. track from A cannot be paired with enrollment B ----------------

    def test_track_from_wrong_academy_rejected(self):
        from curriculum.tests.factories import TrackFactory

        track_a = TrackFactory(organization=self.academy_a)
        enrollment = StudentEnrollment(
            organization=self.academy_b,
            user=self.student,
            track=track_a,
        )
        with self.assertRaises(ValidationError) as ctx:
            enrollment.full_clean()
        self.assertIn("track", ctx.exception.message_dict)

    # ---- 5. level from A cannot be paired with enrollment B ----------------

    def test_level_from_wrong_track_rejected(self):
        from curriculum.tests.factories import TrackFactory, LevelFactory

        track_a = TrackFactory(organization=self.academy_a)
        track_b = TrackFactory(organization=self.academy_a)
        level_b = LevelFactory(track=track_b)

        enrollment = StudentEnrollment(
            organization=self.academy_a,
            user=self.student,
            track=track_a,
            level=level_b,
        )
        with self.assertRaises(ValidationError) as ctx:
            enrollment.full_clean()
        self.assertIn("level", ctx.exception.message_dict)

    def test_level_from_different_academy_rejected_via_track(self):
        """Level belongs to academy B's track → cannot pair with academy A enrollment."""
        from curriculum.tests.factories import TrackFactory, LevelFactory

        track_a = TrackFactory(organization=self.academy_a)
        track_b = TrackFactory(organization=self.academy_b)
        level_b = LevelFactory(track=track_b)

        enrollment = StudentEnrollment(
            organization=self.academy_a,
            user=self.student,
            track=track_a,
            level=level_b,
        )
        with self.assertRaises(ValidationError) as ctx:
            enrollment.full_clean()
        self.assertIn("level", ctx.exception.message_dict)

    # ---- 6. suspended/inactive enrollment behavior is deterministic --------

    def test_inactive_enrollment_is_not_active(self):
        enrollment = StudentEnrollment.objects.create(
            organization=self.academy_a,
            user=self.student,
            status=EnrollmentStatus.INACTIVE,
        )
        self.assertFalse(enrollment.is_active)

    def test_enrollment_status_transitions(self):
        """An enrollment can move active → inactive → active deterministically."""
        enrollment = StudentEnrollment.objects.create(
            organization=self.academy_a,
            user=self.student,
            status=EnrollmentStatus.ACTIVE,
        )
        self.assertTrue(enrollment.is_active)

        enrollment.status = EnrollmentStatus.INACTIVE
        enrollment.save()
        enrollment.refresh_from_db()
        self.assertFalse(enrollment.is_active)

        enrollment.status = EnrollmentStatus.ACTIVE
        enrollment.save()
        enrollment.refresh_from_db()
        self.assertTrue(enrollment.is_active)

    def test_enrollment_requires_student_role(self):
        """Only users with role 'student' can be enrolled."""
        non_student = UserFactory(role=Role.LEAD)
        enrollment = StudentEnrollment(
            organization=self.academy_a,
            user=non_student,
        )
        with self.assertRaises(ValidationError) as ctx:
            enrollment.full_clean()
        self.assertIn("user", ctx.exception.message_dict)

    def test_enrollment_unique_per_academy(self):
        """A student can only have one enrollment per academy."""
        StudentEnrollment.objects.create(
            organization=self.academy_a,
            user=self.student,
        )
        with self.assertRaises(ValidationError):
            StudentEnrollment(
                organization=self.academy_a,
                user=self.student,
            ).full_clean()

    def test_enrollment_across_academies_allowed(self):
        """A student may be enrolled in multiple academies independently."""
        e1 = StudentEnrollment.objects.create(
            organization=self.academy_a,
            user=self.student,
        )
        e2 = StudentEnrollment.objects.create(
            organization=self.academy_b,
            user=self.student,
        )
        self.assertEqual(StudentEnrollment.objects.filter(user=self.student).count(), 2)
        self.assertNotEqual(e1.organization_id, e2.organization_id)

    # ---- invariant: enrollment.organization == track.organization ----------

    def test_track_organization_must_match_enrollment(self):
        """The invariant enrollment.organization == track.organization holds."""
        from curriculum.tests.factories import TrackFactory

        track_b = TrackFactory(organization=self.academy_b)
        enrollment = StudentEnrollment(
            organization=self.academy_a,
            user=self.student,
            track=track_b,
        )
        with self.assertRaises(ValidationError) as ctx:
            enrollment.full_clean()
        self.assertIn("track", ctx.exception.message_dict)
        self.assertIn(
            "different organization",
            ctx.exception.message_dict["track"][0],
        )


class B02EnrollmentIsolationAPITests(APITestCase):
    """API-level tests for cross-tenant enrollment isolation."""

    def setUp(self):
        self.academy_a = OrganizationFactory()
        self.academy_b = OrganizationFactory()
        self.owner_a = UserFactory()
        self.owner_b = UserFactory()
        OrganizationMembershipFactory(
            user=self.owner_a,
            organization=self.academy_a,
            role=OrganizationRole.OWNER,
        )
        OrganizationMembershipFactory(
            user=self.owner_b,
            organization=self.academy_b,
            role=OrganizationRole.OWNER,
        )

    def test_academy_a_cannot_list_academy_b_students(self):
        student = UserFactory(role=Role.STUDENT)
        StudentEnrollment.objects.create(
            organization=self.academy_b, user=student
        )

        self.client.force_authenticate(user=self.owner_a)
        url = reverse(
            "organizations:student-list",
            kwargs={"organization_pk": self.academy_b.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_academy_a_cannot_read_academy_b_enrollment_detail(self):
        student = UserFactory(role=Role.STUDENT)
        enrollment_b = StudentEnrollment.objects.create(
            organization=self.academy_b, user=student
        )

        self.client.force_authenticate(user=self.owner_a)
        url = reverse(
            "organizations:student-detail",
            kwargs={
                "organization_pk": self.academy_a.pk,
                "pk": enrollment_b.pk,
            },
        )
        response = self.client.get(url)
        # 404, not 403 — the row should not be visible at all under academy A
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cross_academy_track_rejected_via_api(self):
        """POST enrollment with a track belonging to a different academy → 400."""
        from curriculum.tests.factories import TrackFactory

        track_b = TrackFactory(organization=self.academy_b)
        student = UserFactory(role=Role.STUDENT)

        self.client.force_authenticate(user=self.owner_a)
        url = reverse(
            "organizations:student-list",
            kwargs={"organization_pk": self.academy_a.pk},
        )
        response = self.client.post(
            url, {"user": student.pk, "track_id": track_b.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cross_academy_level_rejected_via_api(self):
        """PATCH enrollment with a level from a different track → 400."""
        from curriculum.tests.factories import TrackFactory, LevelFactory

        track_a = TrackFactory(organization=self.academy_a)
        track_b = TrackFactory(organization=self.academy_a)
        level_b = LevelFactory(track=track_b)
        student = UserFactory(role=Role.STUDENT)

        enrollment = StudentEnrollment.objects.create(
            organization=self.academy_a,
            user=student,
            track=track_a,
        )

        self.client.force_authenticate(user=self.owner_a)
        url = reverse(
            "organizations:student-detail",
            kwargs={
                "organization_pk": self.academy_a.pk,
                "pk": enrollment.pk,
            },
        )
        response = self.client.patch(
            url, {"level_id": level_b.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inactive_enrollment_visible_but_marked(self):
        """An inactive enrollment still appears in the list, with its status."""
        student = UserFactory(role=Role.STUDENT)
        StudentEnrollment.objects.create(
            organization=self.academy_a,
            user=student,
            status=EnrollmentStatus.INACTIVE,
        )

        self.client.force_authenticate(user=self.owner_a)
        url = reverse(
            "organizations:student-list",
            kwargs={"organization_pk": self.academy_a.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = (
            response.data["results"]
            if "results" in response.data
            else response.data
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["enrollment_status"], "inactive")
