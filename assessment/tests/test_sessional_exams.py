from decimal import Decimal
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role
from accounts.tests.factories import ParentFactory, ParentLinkFactory, StudentFactory
from curriculum.tests.factories import LevelFactory
from organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory
from scheduling.tests.factories import BookableLeadTeacherFactory, BookableTeacherFactory
from assessment.models import (
    ExamQuestionAnswer,
    QuestionType,
    SessionalExam,
    SessionalExamQuestion,
    SessionalExamStatus,
    SessionalExamSubmission,
    SubmissionStatus,
)


class SessionalExamFlowAPITests(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.lead = BookableLeadTeacherFactory()
        OrganizationMembershipFactory(user=self.lead, organization=self.org, role="admin")

        self.teacher = BookableTeacherFactory()
        OrganizationMembershipFactory(user=self.teacher, organization=self.org, role="teacher")

        self.student = StudentFactory()
        OrganizationMembershipFactory(user=self.student, organization=self.org, role="student")

        self.parent = ParentFactory()
        OrganizationMembershipFactory(user=self.parent, organization=self.org, role="parent")
        from accounts.models import ParentLink
        ParentLink.objects.create(parent=self.parent, student=self.student)

        self.level = LevelFactory(track__organization=self.org)
        self.client.force_authenticate(user=self.lead)

    def test_create_sessional_exam_with_questions_and_marks(self):
        url = f"/api/assessment/organizations/{self.org.pk}/sessional-exams/"
        payload = {
            "title": "2026/2027 First Term Tajweed Examination",
            "academic_session": "2026/2027 First Term",
            "track": self.level.track.pk,
            "level": self.level.pk,
            "duration_minutes": 90,
            "total_marks": "100.00",
            "passing_marks": "50.00",
            "status": "published",
            "questions": [
                {
                    "question_number": 1,
                    "question_text": "Recite Surah Al-Mulk (67:1-5) demonstrating clear Makharij",
                    "marks_allotted": "40.00",
                    "question_type": "oral_recitation",
                    "surah_number": 67,
                    "ayah_start": 1,
                    "ayah_end": 5,
                },
                {
                    "question_number": 2,
                    "question_text": "Define Qalqalah and list the five Qalqalah letters",
                    "marks_allotted": "30.00",
                    "question_type": "written",
                },
                {
                    "question_number": 3,
                    "question_text": "Identify the Tajweed rule in Surah Al-Baqarah ayah 10",
                    "marks_allotted": "30.00",
                    "question_type": "tajweed_rules",
                },
            ],
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        exam_id = res.data["id"]
        self.assertEqual(res.data["question_count"], 3)
        self.assertEqual(Decimal(str(res.data["computed_total_marks"])), Decimal("100.00"))

        exam = SessionalExam.objects.get(pk=exam_id)
        self.assertEqual(exam.questions.count(), 3)
        q1 = exam.questions.get(question_number=1)
        self.assertEqual(q1.marks_allotted, Decimal("40.00"))

    def test_student_takes_exam_and_teacher_grades_with_mark_allotment(self):
        # Create published exam
        exam = SessionalExam.objects.create(
            organization=self.org,
            created_by=self.lead,
            title="Hifz Mid-Session Exam",
            academic_session="2026/2027 Mid-Session",
            total_marks=Decimal("50.00"),
            passing_marks=Decimal("25.00"),
            status=SessionalExamStatus.PUBLISHED,
        )
        q1 = SessionalExamQuestion.objects.create(
            exam=exam,
            question_number=1,
            question_text="Recite Surah An-Naba",
            marks_allotted=Decimal("30.00"),
            question_type=QuestionType.ORAL_RECITATION,
        )
        q2 = SessionalExamQuestion.objects.create(
            exam=exam,
            question_number=2,
            question_text="Explain meaning of Ayah 1-5",
            marks_allotted=Decimal("20.00"),
            question_type=QuestionType.WRITTEN,
        )

        # 1. Student submits answers
        self.client.force_authenticate(user=self.student)
        submit_url = f"/api/assessment/organizations/{self.org.pk}/sessional-exams/{exam.pk}/submit/"
        sub_payload = {
            "answers": [
                {"question_id": q1.pk, "written_response": "Audio submission recorded"},
                {"question_id": q2.pk, "written_response": "The Great News refers to Resurrection"},
            ]
        }
        res_sub = self.client.post(submit_url, sub_payload, format="json")
        self.assertEqual(res_sub.status_code, status.HTTP_201_CREATED)
        submission_id = res_sub.data["id"]

        # 2. Teacher grades question-by-question
        self.client.force_authenticate(user=self.teacher)
        grade_url = f"/api/assessment/organizations/{self.org.pk}/sessional-exams/{exam.pk}/submissions/{submission_id}/grade/"

        # Test error if mark awarded exceeds mark allotted
        invalid_grade_payload = {
            "grades": [
                {"question_id": q1.pk, "marks_awarded": "35.00", "teacher_comment": "Too high"},
            ],
            "general_feedback": "Invalid grading",
        }
        res_invalid = self.client.post(grade_url, invalid_grade_payload, format="json")
        self.assertEqual(res_invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cannot exceed mark allotted", res_invalid.data["detail"])

        # Valid grading
        valid_grade_payload = {
            "grades": [
                {"question_id": q1.pk, "marks_awarded": "25.00", "teacher_comment": "Excellent makhraj"},
                {"question_id": q2.pk, "marks_awarded": "18.00", "teacher_comment": "Accurate explanation"},
            ],
            "general_feedback": "Masha'Allah, well prepared and understood.",
        }
        res_grade = self.client.post(grade_url, valid_grade_payload, format="json")
        self.assertEqual(res_grade.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(str(res_grade.data["total_score"])), Decimal("43.00"))
        self.assertEqual(res_grade.data["passed"], True)
        self.assertEqual(Decimal(str(res_grade.data["percentage"])), Decimal("86.00"))

        # 3. Parent reads ward's sessional exam report
        self.client.force_authenticate(user=self.parent)
        ward_url = f"/api/assessment/organizations/{self.org.pk}/sessional-exams/ward/{self.student.pk}/"
        res_ward = self.client.get(ward_url)
        self.assertEqual(res_ward.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_ward.data), 1)
        self.assertEqual(Decimal(str(res_ward.data[0]["total_score"])), Decimal("43.00"))
        self.assertEqual(len(res_ward.data[0]["answers"]), 2)
