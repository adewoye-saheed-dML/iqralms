"""Unit tests for Continuous Assignments and Student Submissions."""

import io
from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import ParentLink, Role, User
from assessment.models import (
    AssignmentSubmission,
    StudentAssignment,
    SubmissionStatus,
    SubmissionType,
)
from curriculum.models import Track
from organizations.models import (
    EnrollmentStatus,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    StudentEnrollment,
)


class AssignmentAndSubmissionTests(APITestCase):
    def setUp(self):
        # 1. Create Organization
        self.org = Organization.objects.create(name="Al-Furqan Academy", slug="al-furqan", timezone="UTC")
        self.other_org = Organization.objects.create(name="Other Academy", slug="other", timezone="UTC")

        # 2. Create Track
        self.track = Track.objects.create(organization=self.org, name="Tajweed Track", slug="tajweed")

        # 3. Create Users
        self.owner = User.objects.create_user(username="owner1", role=Role.LEAD)
        OrganizationMembership.objects.create(
            user=self.owner, organization=self.org, role=OrganizationRole.OWNER, status=MembershipStatus.ACTIVE
        )

        self.teacher = User.objects.create_user(username="teacher1", role=Role.SUB)
        OrganizationMembership.objects.create(
            user=self.teacher, organization=self.org, role=OrganizationRole.TEACHER, status=MembershipStatus.ACTIVE
        )

        self.student = User.objects.create_user(username="student1", role=Role.STUDENT)
        OrganizationMembership.objects.create(
            user=self.student, organization=self.org, role=OrganizationRole.STAFF, status=MembershipStatus.ACTIVE
        )
        StudentEnrollment.objects.create(
            user=self.student, organization=self.org, status=EnrollmentStatus.ACTIVE
        )

        self.parent = User.objects.create_user(username="parent1", role=Role.PARENT)
        OrganizationMembership.objects.create(
            user=self.parent, organization=self.org, role=OrganizationRole.STAFF, status=MembershipStatus.ACTIVE
        )
        ParentLink.objects.create(parent=self.parent, student=self.student)

        self.stranger = User.objects.create_user(username="stranger", role=Role.STUDENT)
        OrganizationMembership.objects.create(
            user=self.stranger, organization=self.other_org, role=OrganizationRole.STAFF, status=MembershipStatus.ACTIVE
        )
        StudentEnrollment.objects.create(
            user=self.stranger, organization=self.other_org, status=EnrollmentStatus.ACTIVE
        )

    def test_teacher_can_create_assignment(self):
        self.client.force_authenticate(user=self.teacher)
        url = f"/api/assessment/organizations/{self.org.id}/assignments/"
        payload = {
            "title": "Surah Al-Fatihah Recitation Practice",
            "description": "Recite with proper makhraj and tajweed rules.",
            "submission_type": SubmissionType.AUDIO_RECITATION,
            "surah_number": 1,
            "ayah_start": 1,
            "ayah_end": 7,
            "max_score": 100,
            "track": self.track.id,
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["title"], "Surah Al-Fatihah Recitation Practice")
        self.assertEqual(res.data["created_by"]["username"], "teacher1")

    def test_student_cannot_create_assignment(self):
        self.client.force_authenticate(user=self.student)
        url = f"/api/assessment/organizations/{self.org.id}/assignments/"
        payload = {"title": "Illegal Assignment", "submission_type": "written_text"}
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_can_submit_and_teacher_can_grade(self):
        # 1. Create Assignment
        assignment = StudentAssignment.objects.create(
            organization=self.org,
            created_by=self.teacher,
            title="Ayatul Kursi Memorization",
            submission_type=SubmissionType.MIXED,
            surah_number=2,
            ayah_start=255,
            ayah_end=255,
            max_score=100,
        )

        # 2. Student Submits
        self.client.force_authenticate(user=self.student)
        submit_url = f"/api/assessment/organizations/{self.org.id}/assignments/{assignment.id}/submit/"
        submit_payload = {
            "written_response": "I recited Ayatul Kursi from memory focusing on the madd letters.",
        }
        res = self.client.post(submit_url, submit_payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        submission_id = res.data["id"]
        self.assertEqual(res.data["status"], SubmissionStatus.SUBMITTED)

        # 3. Teacher Grades
        self.client.force_authenticate(user=self.teacher)
        grade_url = f"/api/assessment/organizations/{self.org.id}/submissions/{submission_id}/grade/"
        grade_payload = {
            "score": "95.00",
            "teacher_feedback": "Masha Allah, excellent pronunciation and smooth recitation!",
            "rubric_scores": [{"criterion_name": "Makhraj", "score": 5, "comment": "Clear letters"}],
        }
        grade_res = self.client.post(grade_url, grade_payload, format="json")
        if grade_res.status_code != 200:
            print("GRADE RES DATA:", grade_res.data)
        self.assertEqual(grade_res.status_code, status.HTTP_200_OK)
        self.assertEqual(grade_res.data["status"], SubmissionStatus.GRADED)
        self.assertEqual(grade_res.data["score"], "95.00")
        self.assertEqual(grade_res.data["graded_by"]["username"], "teacher1")

    def test_parent_and_student_ward_progress(self):
        assignment = StudentAssignment.objects.create(
            organization=self.org,
            created_by=self.teacher,
            title="Tajweed Noon Sakinah Test",
            max_score=100,
        )
        submission = AssignmentSubmission.objects.create(
            assignment=assignment,
            student=self.student,
            written_response="Test response",
            status=SubmissionStatus.GRADED,
            score=Decimal("90.00"),
            teacher_feedback="Great job!",
            graded_by=self.teacher,
            graded_at=timezone.now(),
        )

        # Parent views ward progress
        self.client.force_authenticate(user=self.parent)
        url = f"/api/assessment/organizations/{self.org.id}/ward-progress/?student_id={self.student.id}"
        res = self.client.get(url)
        if res.status_code != 200:
            print("WARD PROGRESS DATA:", res.data)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["total_assigned"], 1)
        self.assertEqual(res.data["total_graded"], 1)
        self.assertEqual(res.data["average_score_pct"], 90.0)
        self.assertEqual(len(res.data["recent_submissions"]), 1)

    def test_owner_can_view_all_academy_submissions(self):
        assignment = StudentAssignment.objects.create(
            organization=self.org,
            created_by=self.teacher,
            title="Homework 1",
            max_score=100,
        )
        AssignmentSubmission.objects.create(
            assignment=assignment,
            student=self.student,
            written_response="Submission 1",
            status=SubmissionStatus.SUBMITTED,
        )

        self.client.force_authenticate(user=self.owner)
        url = f"/api/assessment/organizations/{self.org.id}/submissions/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_cross_tenant_isolation(self):
        assignment = StudentAssignment.objects.create(
            organization=self.org,
            created_by=self.teacher,
            title="Academy A Task",
            max_score=100,
        )
        self.client.force_authenticate(user=self.stranger)
        url = f"/api/assessment/organizations/{self.org.id}/assignments/{assignment.id}/submit/"
        res = self.client.post(url, {"written_response": "Illegal submission"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
