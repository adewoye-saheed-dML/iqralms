from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from accounts.models import Role
from accounts.tests.factories import UserFactory
from organizations.models import StudentEnrollment, EnrollmentStatus
from organizations.tests.factories import OrganizationFactory

class StudentEnrollmentTests(TestCase):
    def test_student_enrollment_creation(self):
        organization = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        
        enrollment = StudentEnrollment.objects.create(
            organization=organization,
            user=student,
            status=EnrollmentStatus.ACTIVE,
        )
        
        self.assertTrue(enrollment.is_active)
        self.assertEqual(str(enrollment), f"{student.username} enrolled in {organization.slug} (active)")

    def test_student_enrollment_requires_student_role(self):
        organization = OrganizationFactory()
        parent = UserFactory(role=Role.PARENT)
        
        enrollment = StudentEnrollment(
            organization=organization,
            user=parent,
            status=EnrollmentStatus.ACTIVE,
        )
        
        with self.assertRaises(ValidationError) as exc_info:
            enrollment.full_clean()
            
        self.assertIn("user", exc_info.exception.message_dict)

    def test_unique_academy_student_constraint(self):
        organization = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        
        StudentEnrollment.objects.create(
            organization=organization,
            user=student,
            status=EnrollmentStatus.ACTIVE,
        )
        
        with self.assertRaises(ValidationError):
            enrollment2 = StudentEnrollment(
                organization=organization,
                user=student,
                status=EnrollmentStatus.INACTIVE,
            )
            enrollment2.full_clean()

    def test_multi_academy_student(self):
        org1 = OrganizationFactory()
        org2 = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        
        e1 = StudentEnrollment.objects.create(organization=org1, user=student)
        e2 = StudentEnrollment.objects.create(organization=org2, user=student)
        
        self.assertEqual(StudentEnrollment.objects.count(), 2)
        self.assertListEqual(list(student.organization_enrollments.all()), [e1, e2])

    def test_enrollment_track_must_belong_to_same_organization(self):
        from curriculum.tests.factories import TrackFactory
        org1 = OrganizationFactory()
        org2 = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        track = TrackFactory(organization=org2)
        
        enrollment = StudentEnrollment(
            organization=org1,
            user=student,
            track=track,
        )
        with self.assertRaises(ValidationError) as exc_info:
            enrollment.full_clean()
        self.assertIn("track", exc_info.exception.message_dict)

    def test_enrollment_level_requires_track(self):
        from curriculum.tests.factories import LevelFactory
        org = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        level = LevelFactory(track__organization=org)
        
        enrollment = StudentEnrollment(
            organization=org,
            user=student,
            level=level,
        )
        with self.assertRaises(ValidationError) as exc_info:
            enrollment.full_clean()
        self.assertIn("level", exc_info.exception.message_dict)
        self.assertEqual(
            exc_info.exception.message_dict["level"][0],
            "Cannot set a level without a track."
        )

    def test_enrollment_level_must_belong_to_enrollment_track(self):
        from curriculum.tests.factories import LevelFactory, TrackFactory
        org = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        track1 = TrackFactory(organization=org)
        track2 = TrackFactory(organization=org)
        level2 = LevelFactory(track=track2)
        
        enrollment = StudentEnrollment(
            organization=org,
            user=student,
            track=track1,
            level=level2,
        )
        with self.assertRaises(ValidationError) as exc_info:
            enrollment.full_clean()
        self.assertIn("level", exc_info.exception.message_dict)
        self.assertEqual(
            exc_info.exception.message_dict["level"][0],
            "The level belongs to a different track."
        )
