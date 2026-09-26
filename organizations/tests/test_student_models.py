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

    def test_enrollment_teacher_must_have_teacher_role(self):
        org = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        non_teacher = UserFactory(role=Role.PARENT)

        enrollment = StudentEnrollment(
            organization=org,
            user=student,
            teacher=non_teacher,
        )
        with self.assertRaises(ValidationError) as exc_info:
            enrollment.full_clean()
        self.assertIn("teacher", exc_info.exception.message_dict)
        self.assertEqual(
            exc_info.exception.message_dict["teacher"][0],
            "Only users with a teacher role can be assigned as instructors."
        )

    def test_enrollment_teacher_must_be_active_org_member(self):
        org = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        teacher = UserFactory(role=Role.SUB)

        enrollment = StudentEnrollment(
            organization=org,
            user=student,
            teacher=teacher,
        )
        with self.assertRaises(ValidationError) as exc_info:
            enrollment.full_clean()
        self.assertIn("teacher", exc_info.exception.message_dict)
        self.assertEqual(
            exc_info.exception.message_dict["teacher"][0],
            "The assigned teacher does not have an active membership in this academy."
        )

    def test_enrollment_teacher_must_be_eligible_for_track(self):
        from organizations.models import OrganizationMembership, OrganizationRole
        from curriculum.tests.factories import TrackFactory

        org = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        teacher = UserFactory(role=Role.SUB)
        OrganizationMembership.objects.create(
            organization=org, user=teacher, role=OrganizationRole.TEACHER
        )
        track = TrackFactory(organization=org)

        enrollment = StudentEnrollment(
            organization=org,
            user=student,
            track=track,
            teacher=teacher,
        )
        with self.assertRaises(ValidationError) as exc_info:
            enrollment.full_clean()
        self.assertIn("teacher", exc_info.exception.message_dict)
        self.assertIn("is not authorized to teach", exc_info.exception.message_dict["teacher"][0])

    def test_valid_teacher_allocation(self):
        from organizations.models import OrganizationMembership, OrganizationRole
        from curriculum.models import TeacherTrack
        from curriculum.tests.factories import TrackFactory, LevelFactory

        org = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        teacher = UserFactory(role=Role.SUB)
        mem = OrganizationMembership.objects.create(
            organization=org, user=teacher, role=OrganizationRole.TEACHER
        )
        track = TrackFactory(organization=org)
        level = LevelFactory(track=track)
        TeacherTrack.objects.create(membership=mem, track=track, active=True)

        enrollment = StudentEnrollment.objects.create(
            organization=org,
            user=student,
            track=track,
            level=level,
            teacher=teacher,
        )
        self.assertEqual(enrollment.teacher, teacher)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.teacher_id, teacher.id)
