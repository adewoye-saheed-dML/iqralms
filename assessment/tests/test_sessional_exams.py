import json
from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
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

    def test_sessional_exam_audio_submission_and_streaming(self):
        """Verify student can submit audio answers via multipart upload without SuspiciousOperation,
        and audio can be streamed securely by authorized roles."""
        exam = SessionalExam.objects.create(
            organization=self.org,
            level=self.level,
            title="Oral Recitation Examination",
            academic_session="2026/2027",
            status=SessionalExamStatus.PUBLISHED,
            total_marks=Decimal("50.00"),
            passing_marks=Decimal("25.00"),
            created_by=self.lead,
        )
        q1 = SessionalExamQuestion.objects.create(
            exam=exam,
            question_number=1,
            question_text="Recite Surah Al-Mulk",
            marks_allotted=Decimal("50.00"),
            question_type=QuestionType.ORAL_RECITATION,
        )

        # 1. Student submits with audio file
        self.client.force_authenticate(user=self.student)
        submit_url = f"/api/assessment/organizations/{self.org.pk}/sessional-exams/{exam.pk}/submit/"
        audio_content = b"\x1a\x45\xdf\xa3\x01\x00\x00\x00" + b"\x00" * 64
        audio_file = SimpleUploadedFile("audio_question_1.webm", audio_content, content_type="audio/webm")

        payload = {
            "answers": json.dumps([
                {"question_id": q1.pk, "written_response": "Recitation completed."}
            ]),
            f"audio_question_{q1.pk}": audio_file,
        }

        res_sub = self.client.post(submit_url, payload, format="multipart")
        self.assertEqual(res_sub.status_code, status.HTTP_201_CREATED)
        sub_data = res_sub.data
        self.assertIn("answers", sub_data)
        self.assertEqual(len(sub_data["answers"]), 1)

        ans_data = sub_data["answers"][0]
        answer_id = ans_data["id"]
        submission_id = sub_data["id"]

        # Check audio_recording and audio_recording_url are generated without SuspiciousOperation
        expected_audio_path = (
            f"/api/assessment/organizations/{self.org.pk}/sessional-exams/{exam.pk}"
            f"/submissions/{submission_id}/answers/{answer_id}/audio/"
        )
        self.assertIsNotNone(ans_data["audio_recording"])
        self.assertIn(expected_audio_path, ans_data["audio_recording"])
        self.assertIn(expected_audio_path, ans_data["audio_recording_url"])

        # 2. Student streams the audio
        audio_url = ans_data["audio_recording"]
        res_audio = self.client.get(audio_url)
        self.assertEqual(res_audio.status_code, status.HTTP_200_OK)
        self.assertEqual(res_audio.headers.get("Content-Type"), "audio/webm")
        self.assertEqual(b"".join(res_audio.streaming_content), audio_content)

        # 3. Teacher streams the audio
        self.client.force_authenticate(user=self.teacher)
        res_teacher_audio = self.client.get(audio_url)
        self.assertEqual(res_teacher_audio.status_code, status.HTTP_200_OK)

        # 4. Parent streams the audio
        self.client.force_authenticate(user=self.parent)
        res_parent_audio = self.client.get(audio_url)
        self.assertEqual(res_parent_audio.status_code, status.HTTP_200_OK)

        # 5. Outside student cannot stream the audio
        other_student = StudentFactory()
        OrganizationMembershipFactory(user=other_student, organization=self.org, role="student")
        self.client.force_authenticate(user=other_student)
        res_other = self.client.get(audio_url)
        self.assertEqual(res_other.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_review_and_approval_flow_for_teacher_sessional_exam(self):
        """Verify that exams set by teachers must be reviewed and approved by the owner
        before students can view or submit them."""
        # 1. Teacher attempts to publish directly -> rejected
        self.client.force_authenticate(user=self.teacher)
        exam_list_url = f"/api/assessment/organizations/{self.org.pk}/sessional-exams/"
        direct_publish_payload = {
            "title": "Midterm Recitation Test",
            "academic_session": "2026/2027 First Term",
            "status": SessionalExamStatus.PUBLISHED,
            "questions": [
                {
                    "question_number": 1,
                    "question_text": "Recite Surah Al-Fajr",
                    "marks_allotted": "50.00",
                    "question_type": QuestionType.ORAL_RECITATION,
                }
            ],
        }
        res_direct = self.client.post(exam_list_url, direct_publish_payload, format="json")
        self.assertEqual(res_direct.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Only the academy owner or administrator can approve and publish", str(res_direct.data))

        # 2. Teacher sets exam with status='pending_approval'
        pending_payload = {
            "title": "Midterm Recitation Test",
            "academic_session": "2026/2027 First Term",
            "status": SessionalExamStatus.PENDING_APPROVAL,
            "questions": [
                {
                    "question_number": 1,
                    "question_text": "Recite Surah Al-Fajr",
                    "marks_allotted": "50.00",
                    "question_type": QuestionType.ORAL_RECITATION,
                }
            ],
        }
        res_create = self.client.post(exam_list_url, pending_payload, format="json")
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        exam_id = res_create.data["id"]
        self.assertEqual(res_create.data["status"], SessionalExamStatus.PENDING_APPROVAL)

        # 3. Student cannot see pending exam in listing or submit to it
        self.client.force_authenticate(user=self.student)
        res_student_list = self.client.get(exam_list_url)
        self.assertEqual(res_student_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_student_list.data), 0)

        submit_url = f"/api/assessment/organizations/{self.org.pk}/sessional-exams/{exam_id}/submit/"
        res_student_submit = self.client.post(
            submit_url,
            {"answers": [{"question_id": res_create.data["questions"][0]["id"], "written_response": "done"}]},
            format="json",
        )
        self.assertEqual(res_student_submit.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("has not yet been approved and published by the academy owner", res_student_submit.data["detail"])

        # 4. Non-owner (teacher) cannot call the review endpoint
        self.client.force_authenticate(user=self.teacher)
        review_url = f"/api/assessment/organizations/{self.org.pk}/sessional-exams/{exam_id}/review/"
        res_teacher_review = self.client.post(review_url, {"action": "approve"}, format="json")
        self.assertEqual(res_teacher_review.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Owner requests changes (reject)
        self.client.force_authenticate(user=self.lead)
        res_owner_reject = self.client.post(
            review_url,
            {
                "action": "reject",
                "review_comments": "Please add surah ayah range for question 1.",
            },
            format="json",
        )
        self.assertEqual(res_owner_reject.status_code, status.HTTP_200_OK)
        self.assertEqual(res_owner_reject.data["status"], SessionalExamStatus.REJECTED)
        self.assertEqual(res_owner_reject.data["review_comments"], "Please add surah ayah range for question 1.")

        # 6. Teacher updates question and resubmits for approval
        self.client.force_authenticate(user=self.teacher)
        detail_url = f"/api/assessment/organizations/{self.org.pk}/sessional-exams/{exam_id}/"
        res_update = self.client.patch(
            detail_url,
            {"status": SessionalExamStatus.PENDING_APPROVAL},
            format="json",
        )
        self.assertEqual(res_update.status_code, status.HTTP_200_OK)
        self.assertEqual(res_update.data["status"], SessionalExamStatus.PENDING_APPROVAL)

        # 7. Owner approves and publishes the exam
        self.client.force_authenticate(user=self.lead)
        res_owner_approve = self.client.post(
            review_url,
            {
                "action": "approve",
                "review_comments": "Approved. Ayah range looks good.",
            },
            format="json",
        )
        self.assertEqual(res_owner_approve.status_code, status.HTTP_200_OK)
        self.assertEqual(res_owner_approve.data["status"], SessionalExamStatus.PUBLISHED)
        self.assertEqual(res_owner_approve.data["reviewed_by_name"], self.lead.get_full_name())
        self.assertIsNotNone(res_owner_approve.data["reviewed_at"])

        # 8. Student can now see and submit the published exam
        self.client.force_authenticate(user=self.student)
        res_student_list_after = self.client.get(exam_list_url)
        self.assertEqual(res_student_list_after.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_student_list_after.data), 1)

        res_student_take = self.client.post(
            submit_url,
            {"answers": [{"question_id": res_create.data["questions"][0]["id"], "written_response": "Surah recited."}]},
            format="json",
        )
        self.assertEqual(res_student_take.status_code, status.HTTP_201_CREATED)
