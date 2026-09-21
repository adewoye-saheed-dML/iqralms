"""Tests for assessment and progress role scoping stabilization (Sections 5 & 6 of IQRA_BE_ROLE_ONBOARDING_CLASS_STABILIZATION.md)."""

from datetime import timedelta
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import ParentLink, Role
from accounts.tests.factories import ParentFactory, StudentFactory, SubTeacherFactory, UserFactory
from assessment.models import AssessmentRubric, ProgressSnapshot, SessionAssessment
from curriculum.tests.factories import LevelFactory, TrackFactory
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole, StudentEnrollment
from organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory
from scheduling.models import Booking, BookingStatus


class RoleAssessmentStabilizationTests(APITestCase):
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
        StudentEnrollment.objects.create(
            organization=self.org,
            user=self.student,
            track=self.track,
            level=self.level,
        )

        # Booking between sub_teacher and student
        self.booking = Booking.objects.bulk_create([
            Booking(
                student=self.student,
                teacher=self.sub_teacher,
                level=self.level,
                start_time_utc=timezone.now() - timedelta(days=2),
                duration_minutes=30,
                status=BookingStatus.COMPLETED,
            )
        ])[0]

        # Rubric for the track
        self.rubric = AssessmentRubric.objects.create(track=self.track, name="Tajweed Rubric")

        # Create a submitted assessment directly
        self.assessment = SessionAssessment.objects.bulk_create([
            SessionAssessment(
                booking=self.booking,
                student=self.student,
                track=self.track,
                assessed_by=self.sub_teacher,
                rubric_name="Tajweed Rubric",
                criteria_snapshot=[{"name": "Fluency", "max_score": 5}],
                teacher_summary="Steady progress",
                flagged_for_review=True,
                flag_reason="Needs review",
            )
        ])[0]

    # --- Section 5: Review Queue & Permissions ---

    def test_review_queue_permissions(self):
        url = reverse("assessment:review-queue", args=[self.org.pk])

        # 1. Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 2. Admin allowed
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 3. Lead teacher allowed
        self.client.force_authenticate(user=self.lead_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 4. Ordinary sub-teacher denied
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

    def test_assessment_detail_reviewer_permissions(self):
        url = reverse("assessment:assessment-detail", args=[self.org.pk, self.assessment.pk])

        # Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Admin allowed
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Lead Teacher allowed
        self.client.force_authenticate(user=self.lead_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Ordinary sub-teacher denied
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_model_clean_lead_reviewed_by(self):
        self.assessment.lead_review_notes = "Great review"
        self.assessment.lead_reviewed_at = timezone.now()

        # 1. Owner allowed
        self.assessment.lead_reviewed_by = self.owner
        errors = {}
        self.assessment._validate_lead_review(errors)
        self.assertEqual(errors, {})

        # 2. Admin allowed
        self.assessment.lead_reviewed_by = self.admin_user
        errors = {}
        self.assessment._validate_lead_review(errors)
        self.assertEqual(errors, {})

        # 3. Lead teacher allowed
        self.assessment.lead_reviewed_by = self.lead_teacher
        errors = {}
        self.assessment._validate_lead_review(errors)
        self.assertEqual(errors, {})

        # 4. Sub-teacher raises ValidationError
        self.assessment.lead_reviewed_by = self.sub_teacher
        errors = {}
        self.assessment._validate_lead_review(errors)
        self.assertIn("lead_reviewed_by", errors)

    # --- Section 6: Progress Reads ---

    def test_student_progress_mine(self):
        url = reverse("assessment:progress-mine", args=[self.org.pk])

        # Student allowed
        self.client.force_authenticate(user=self.student)
        res = self.client.get(url, {"track_id": self.track.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["track"]["id"], self.track.id)

        # Teacher denied
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url, {"track_id": self.track.id})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_parent_progress_child(self):
        url = reverse("assessment:progress-child", args=[self.org.pk])

        # Parent of student allowed
        self.client.force_authenticate(user=self.parent)
        res = self.client.get(url, {"student_id": self.student.id, "track_id": self.track.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["track"]["id"], self.track.id)

        # Unrelated parent denied
        unrelated_parent = ParentFactory()
        OrganizationMembershipFactory(
            organization=self.org,
            user=unrelated_parent,
            role=OrganizationRole.PARENT,
            status=MembershipStatus.ACTIVE,
        )
        self.client.force_authenticate(user=unrelated_parent)
        res = self.client.get(url, {"student_id": self.student.id, "track_id": self.track.id})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_teaching_progress_scoping(self):
        url = reverse("assessment:progress-teaching", args=[self.org.pk])

        # 1. Teacher with assigned booking allowed
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url, {"student_id": self.student.id, "track_id": self.track.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["track"]["id"], self.track.id)

        # 2. Teacher with NO booking denied
        self.client.force_authenticate(user=self.other_teacher)
        res = self.client.get(url, {"student_id": self.student.id, "track_id": self.track.id})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("teaching relationship", str(res.data).lower())

        # 3. Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url, {"student_id": self.student.id, "track_id": self.track.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["track"]["id"], self.track.id)

        # 4. Admin allowed
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(url, {"student_id": self.student.id, "track_id": self.track.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["track"]["id"], self.track.id)

    # --- Section 6: Progress Snapshots Permissions ---

    def test_snapshot_permissions_and_clean(self):
        url = reverse("assessment:snapshot-list", args=[self.org.pk])

        # Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Admin allowed
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Lead teacher allowed
        self.client.force_authenticate(user=self.lead_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Sub-teacher denied
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
