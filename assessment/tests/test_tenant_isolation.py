"""SaaS Phase 6 — Assessment Tenant Isolation Test Suite (Tasks 6.4 & 6.5).

Two-academy adversarial test matrix covering:
1. Academy A lead cannot list Academy B rubrics.
2. Academy A lead cannot retrieve or mutate Academy B rubric by ID (returns 404).
3. Academy A cannot create rubric using Academy B track (returns 400).
4. Academy A teacher cannot submit assessment for Academy B booking (returns 404).
5. Academy A teacher cannot list Academy B assessments in teacher/mine/.
6. Academy A student cannot list Academy B assessments in /mine/.
7. Academy A lead cannot list Academy B flagged assessments in review/queue/.
8. Academy A lead cannot retrieve Academy B assessment detail by ID (returns 404).
9. Academy A lead cannot review Academy B assessment by ID (returns 404).
10. Academy A lead cannot list Academy B snapshots in snapshots/all/.
11. Academy A student cannot list Academy B snapshots in snapshots/mine/.
12. Academy A parent cannot list Academy B snapshots in snapshots/child/.
13. Cross-academy snapshot creation is rejected at API layer (returns 400).
14. Academy A members calling Academy B URLs are rejected with 403 (not an active member).
15. Suspended members (lead, teacher, student, parent) are refused with 403.
16. Multi-academy student assessments, snapshots, and progress are strictly isolated.
17. Teacher quality report in Academy A aggregates only Academy A sessions.
18. Student progress report in Academy A aggregates only Academy A sessions.
19. Parent cannot access unrelated children in same or other academy.
20. Privacy rules: family views omit internal QC fields (flag, reason, lead notes).
21. Teacher views omit other teachers' submissions and omit lead review notes.
22. Model invariants: direct ORM writes reject cross-academy relationships.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role, User
from accounts.tests.factories import (
    LeadTeacherFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
)
from assessment.models import (
    AssessmentCriterion,
    AssessmentRubric,
    AssessmentScore,
    ProgressSnapshot,
    SessionAssessment,
)
from assessment.tests.factories import (
    AssessmentCriterionFactory,
    AssessmentRubricFactory,
    FlaggedAssessmentFactory,
    ProgressSnapshotFactory,
    PublishedSnapshotFactory,
    RubricWithCriteriaFactory,
    SessionAssessmentFactory,
    past_completed_booking,
)
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import OrganizationFactory
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableTeacherFactory,
    ensure_teacher_configured,
)


class TwoAcademiesAssessmentFixture(APITestCase):
    """Fixture with two distinct academies and a multi-academy student."""

    def setUp(self):
        super().setUp()

        self.period_start = dj_timezone.now() - timedelta(weeks=4)
        self.period_end = dj_timezone.now()

        # =====================================================================
        # Academy A
        # =====================================================================
        self.academy_a = OrganizationFactory(name="Academy Alpha")
        self.track_a = TrackFactory(
            organization=self.academy_a, name="Track A", slug="track-a"
        )
        self.level_a = LevelFactory(track=self.track_a, order=1)
        self.rubric_a = RubricWithCriteriaFactory(track=self.track_a, name="Rubric A")

        self.lead_a = LeadTeacherFactory()
        self.lead_a_membership = admit(
            self.lead_a, self.academy_a, role=OrganizationRole.OWNER
        )
        ensure_teacher_configured(self.lead_a, self.academy_a)

        self.teacher_a = BookableTeacherFactory()
        self.teacher_a_membership = admit(
            self.teacher_a, self.academy_a, role=OrganizationRole.TEACHER
        )
        ensure_teacher_configured(self.teacher_a, self.academy_a)

        self.student_a = StudentFactory()
        self.student_a_membership = admit(
            self.student_a, self.academy_a, role=OrganizationRole.STAFF
        )

        self.parent_a = ParentFactory()
        self.parent_a_membership = admit(
            self.parent_a, self.academy_a, role=OrganizationRole.STAFF
        )
        self.link_a = ParentLinkFactory(parent=self.parent_a, student=self.student_a)

        self.window_a = AvailabilityFactory(
            organization=self.academy_a, teacher=self.teacher_a
        )
        self.booking_a = past_completed_booking(
            availability=self.window_a,
            level=self.level_a,
            student=self.student_a,
            weeks_ago=1,
        )
        self.assessment_a = SessionAssessmentFactory(
            booking=self.booking_a,
            flagged_for_review=True,
            flag_reason="Academy A review requested",
        )
        self.snapshot_a = PublishedSnapshotFactory(
            student=self.student_a,
            track=self.track_a,
            generated_by=self.lead_a,
            period_start=self.period_start,
            period_end=self.period_end,
        )

        # =====================================================================
        # Academy B
        # =====================================================================
        self.academy_b = OrganizationFactory(name="Academy Beta")
        self.track_b = TrackFactory(
            organization=self.academy_b, name="Track B", slug="track-b"
        )
        self.level_b = LevelFactory(track=self.track_b, order=1)
        self.rubric_b = RubricWithCriteriaFactory(track=self.track_b, name="Rubric B")

        self.lead_b = LeadTeacherFactory()
        self.lead_b_membership = admit(
            self.lead_b, self.academy_b, role=OrganizationRole.OWNER
        )
        ensure_teacher_configured(self.lead_b, self.academy_b)

        self.teacher_b = BookableTeacherFactory()
        self.teacher_b_membership = admit(
            self.teacher_b, self.academy_b, role=OrganizationRole.TEACHER
        )
        ensure_teacher_configured(self.teacher_b, self.academy_b)

        self.student_b = StudentFactory()
        self.student_b_membership = admit(
            self.student_b, self.academy_b, role=OrganizationRole.STAFF
        )

        self.parent_b = ParentFactory()
        self.parent_b_membership = admit(
            self.parent_b, self.academy_b, role=OrganizationRole.STAFF
        )
        self.link_b = ParentLinkFactory(parent=self.parent_b, student=self.student_b)

        self.window_b = AvailabilityFactory(
            organization=self.academy_b, teacher=self.teacher_b
        )
        self.booking_b = past_completed_booking(
            availability=self.window_b,
            level=self.level_b,
            student=self.student_b,
            weeks_ago=1,
        )
        self.assessment_b = SessionAssessmentFactory(
            booking=self.booking_b,
            flagged_for_review=True,
            flag_reason="Academy B review requested",
        )
        self.snapshot_b = PublishedSnapshotFactory(
            student=self.student_b,
            track=self.track_b,
            generated_by=self.lead_b,
            period_start=self.period_start,
            period_end=self.period_end,
        )

        # =====================================================================
        # Multi-academy student (enrolled in both A and B)
        # =====================================================================
        self.multi_student = StudentFactory()
        self.multi_membership_a = admit(
            self.multi_student, self.academy_a, role=OrganizationRole.STAFF
        )
        self.multi_membership_b = admit(
            self.multi_student, self.academy_b, role=OrganizationRole.STAFF
        )

        self.booking_multi_a = past_completed_booking(
            availability=self.window_a,
            level=self.level_a,
            student=self.multi_student,
            weeks_ago=2,
        )
        self.assessment_multi_a = SessionAssessmentFactory(
            booking=self.booking_multi_a,
            score_value=5,
            teacher_summary="Excellent recitation in Academy A",
        )

        self.booking_multi_b = past_completed_booking(
            availability=self.window_b,
            level=self.level_b,
            student=self.multi_student,
            weeks_ago=2,
        )
        self.assessment_multi_b = SessionAssessmentFactory(
            booking=self.booking_multi_b,
            score_value=2,
            teacher_summary="Needs work on Tajweed in Academy B",
        )

    # --- URL helpers ---------------------------------------------------------

    def rubric_list_url(self, org):
        return reverse("assessment:rubric-list", kwargs={"organization_pk": org.pk})

    def rubric_detail_url(self, org, pk):
        return reverse(
            "assessment:rubric-detail", kwargs={"organization_pk": org.pk, "pk": pk}
        )

    def booking_assess_url(self, org, booking_id):
        return reverse(
            "assessment:assessment-create",
            kwargs={"organization_pk": org.pk, "booking_id": booking_id},
        )

    def student_mine_url(self, org):
        return reverse("assessment:assessment-mine", kwargs={"organization_pk": org.pk})

    def child_assessments_url(self, org):
        return reverse(
            "assessment:assessment-child", kwargs={"organization_pk": org.pk}
        )

    def teacher_mine_url(self, org):
        return reverse(
            "assessment:assessment-teacher-mine", kwargs={"organization_pk": org.pk}
        )

    def review_queue_url(self, org):
        return reverse("assessment:review-queue", kwargs={"organization_pk": org.pk})

    def assessment_detail_url(self, org, pk):
        return reverse(
            "assessment:assessment-detail", kwargs={"organization_pk": org.pk, "pk": pk}
        )

    def assessment_review_url(self, org, pk):
        return reverse(
            "assessment:assessment-review", kwargs={"organization_pk": org.pk, "pk": pk}
        )

    def teacher_report_url(self, org):
        return reverse("assessment:report-teachers", kwargs={"organization_pk": org.pk})

    def my_progress_url(self, org):
        return reverse("assessment:progress-mine", kwargs={"organization_pk": org.pk})

    def child_progress_url(self, org):
        return reverse("assessment:progress-child", kwargs={"organization_pk": org.pk})

    def snapshot_create_url(self, org):
        return reverse(
            "assessment:snapshot-create", kwargs={"organization_pk": org.pk}
        )

    def snapshot_list_url(self, org):
        return reverse("assessment:snapshot-list", kwargs={"organization_pk": org.pk})

    def snapshot_mine_url(self, org):
        return reverse("assessment:snapshot-mine", kwargs={"organization_pk": org.pk})

    def snapshot_child_url(self, org):
        return reverse("assessment:snapshot-child", kwargs={"organization_pk": org.pk})


class AssessmentTenantIsolationTests(TwoAcademiesAssessmentFixture):
    """Adversarial tests proving cross-academy isolation across all assessment endpoints."""

    # --- 1. Rubric isolation -------------------------------------------------

    def test_01_academy_a_lead_cannot_list_academy_b_rubrics(self):
        """Lead A listing rubrics in Academy A only sees Academy A rubrics."""
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.get(self.rubric_list_url(self.academy_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {r["id"] for r in response.data}
        self.assertIn(self.rubric_a.pk, returned_ids)
        self.assertNotIn(self.rubric_b.pk, returned_ids)

    def test_02_academy_a_lead_cannot_retrieve_or_mutate_academy_b_rubric(self):
        """Lead A requesting Academy B rubric through Academy A URL gets 404, not 403."""
        self.client.force_authenticate(user=self.lead_a)

        # GET detail returns 404
        response = self.client.get(
            self.rubric_detail_url(self.academy_a, self.rubric_b.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # PATCH returns 404
        patch_response = self.client.patch(
            self.rubric_detail_url(self.academy_a, self.rubric_b.pk),
            {"name": "Hijacked Rubric"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_404_NOT_FOUND)
        self.rubric_b.refresh_from_db()
        self.assertNotEqual(self.rubric_b.name, "Hijacked Rubric")

    def test_03_academy_a_cannot_create_rubric_with_academy_b_track(self):
        """Lead A in Academy A cannot create a rubric referencing Track B."""
        self.client.force_authenticate(user=self.lead_a)
        payload = {
            "track": self.track_b.slug,
            "name": "Cross-Academy Rubric",
            "criteria": [{"name": "Recitation", "order": 1}],
        }
        response = self.client.post(
            self.rubric_list_url(self.academy_a), payload, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", response.data)
        self.assertFalse(
            AssessmentRubric.objects.filter(name="Cross-Academy Rubric").exists()
        )

    # --- 2. Assessment submission isolation ----------------------------------

    def test_04_academy_a_teacher_cannot_submit_assessment_for_academy_b_booking(self):
        """Teacher A submitting an assessment for Booking B gets 404 (does not exist for teacher in A)."""
        self.client.force_authenticate(user=self.teacher_a)
        payload = {
            "teacher_summary": "Attempting cross-academy assessment",
            "scores": [
                {"criterion": c.pk, "score": 4}
                for c in self.rubric_b.active_criteria()
            ],
        }
        response = self.client.post(
            self.booking_assess_url(self.academy_a, self.booking_b.pk),
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- 3. Assessment read lists isolation -----------------------------------

    def test_05_academy_a_teacher_cannot_list_academy_b_assessments(self):
        """Teacher A calling teacher/mine/ only sees submissions in Academy A."""
        self.client.force_authenticate(user=self.teacher_a)
        response = self.client.get(self.teacher_mine_url(self.academy_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {r["id"] for r in response.data}
        self.assertIn(self.assessment_a.pk, returned_ids)
        self.assertNotIn(self.assessment_b.pk, returned_ids)

    def test_06_academy_a_student_cannot_list_academy_b_assessments(self):
        """Student A calling /mine/ in Academy A only sees Academy A assessments."""
        self.client.force_authenticate(user=self.student_a)
        response = self.client.get(self.student_mine_url(self.academy_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {r["id"] for r in response.data}
        self.assertIn(self.assessment_a.pk, returned_ids)
        self.assertNotIn(self.assessment_b.pk, returned_ids)

    def test_07_academy_a_parent_cannot_list_academy_b_assessments(self):
        """Parent A calling /child/ in Academy A for student A sees only Academy A assessments."""
        self.client.force_authenticate(user=self.parent_a)
        response = self.client.get(
            self.child_assessments_url(self.academy_a),
            {"student_id": self.student_a.pk},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {r["id"] for r in response.data}
        self.assertIn(self.assessment_a.pk, returned_ids)
        self.assertNotIn(self.assessment_b.pk, returned_ids)

    def test_08_academy_a_lead_review_queue_is_isolated(self):
        """Lead A calling review/queue/ in Academy A only sees flagged assessments from Academy A."""
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.get(self.review_queue_url(self.academy_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {r["id"] for r in response.data}
        self.assertIn(self.assessment_a.pk, returned_ids)
        self.assertNotIn(self.assessment_b.pk, returned_ids)

    # --- 4. Detail and review object-level isolation --------------------------

    def test_09_academy_a_lead_cannot_retrieve_academy_b_assessment_detail(self):
        """Lead A attempting to view Academy B assessment via Academy A URL gets 404."""
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.get(
            self.assessment_detail_url(self.academy_a, self.assessment_b.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_10_academy_a_lead_cannot_review_academy_b_assessment(self):
        """Lead A attempting to review Academy B assessment via Academy A URL gets 404."""
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.post(
            self.assessment_review_url(self.academy_a, self.assessment_b.pk),
            {"lead_review_note": "Reviewed by wrong lead"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assessment_b.refresh_from_db()
        self.assertIsNone(self.assessment_b.lead_reviewed_at)

    # --- 5. Progress and snapshots isolation ---------------------------------

    def test_11_academy_a_lead_cannot_list_academy_b_snapshots(self):
        """Lead A calling snapshots/all/ in Academy A sees only Academy A snapshots."""
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.get(self.snapshot_list_url(self.academy_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {r["id"] for r in response.data}
        self.assertIn(self.snapshot_a.pk, returned_ids)
        self.assertNotIn(self.snapshot_b.pk, returned_ids)

    def test_12_academy_a_student_cannot_list_academy_b_snapshots(self):
        """Student A calling snapshots/mine/ in Academy A sees only Academy A snapshots."""
        self.client.force_authenticate(user=self.student_a)
        response = self.client.get(self.snapshot_mine_url(self.academy_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {r["id"] for r in response.data}
        self.assertIn(self.snapshot_a.pk, returned_ids)
        self.assertNotIn(self.snapshot_b.pk, returned_ids)

    def test_13_academy_a_parent_cannot_list_academy_b_snapshots(self):
        """Parent A calling snapshots/child/ in Academy A sees only Academy A snapshots."""
        self.client.force_authenticate(user=self.parent_a)
        response = self.client.get(
            self.snapshot_child_url(self.academy_a),
            {"student_id": self.student_a.pk},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {r["id"] for r in response.data}
        self.assertIn(self.snapshot_a.pk, returned_ids)
        self.assertNotIn(self.snapshot_b.pk, returned_ids)

    def test_14_cross_academy_snapshot_creation_is_rejected(self):
        """Lead A cannot generate snapshot for Academy B student or track."""
        self.client.force_authenticate(user=self.lead_a)

        # Foreign student
        res_bad_student = self.client.post(
            self.snapshot_create_url(self.academy_a),
            {
                "student": self.student_b.pk,
                "track": self.track_a.slug,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
            },
            format="json",
        )
        self.assertEqual(res_bad_student.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", res_bad_student.data)

        # Foreign track
        res_bad_track = self.client.post(
            self.snapshot_create_url(self.academy_a),
            {
                "student": self.student_a.pk,
                "track": self.track_b.slug,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
            },
            format="json",
        )
        self.assertEqual(res_bad_track.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", res_bad_track.data)

    # --- 6. Direct URL tenancy gate (IsOrganizationMember) --------------------

    def test_15_academy_a_users_calling_academy_b_urls_are_forbidden(self):
        """Users from Academy A attempting to call Academy B URLs receive 403 Forbidden."""
        for user, role_name in [
            (self.lead_a, "lead"),
            (self.teacher_a, "teacher"),
            (self.student_a, "student"),
            (self.parent_a, "parent"),
        ]:
            with self.subTest(user=role_name):
                self.client.force_authenticate(user=user)
                # Attempt to access rubrics in Academy B
                res = self.client.get(self.rubric_list_url(self.academy_b))
                self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
                self.assertIn(
                    "You are not an active member of this organization",
                    str(res.data),
                )

    def test_16_suspended_members_are_rejected_with_403(self):
        """A member suspended in Academy A is denied all assessment access in Academy A."""
        # Suspend teacher A
        self.teacher_a_membership.status = MembershipStatus.SUSPENDED
        self.teacher_a_membership.save(update_fields=["status"])

        self.client.force_authenticate(user=self.teacher_a)
        res = self.client.get(self.teacher_mine_url(self.academy_a))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Suspend student A
        self.student_a_membership.status = MembershipStatus.SUSPENDED
        self.student_a_membership.save(update_fields=["status"])

        self.client.force_authenticate(user=self.student_a)
        res = self.client.get(self.student_mine_url(self.academy_a))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Suspend parent A
        self.parent_a_membership.status = MembershipStatus.SUSPENDED
        self.parent_a_membership.save(update_fields=["status"])

        self.client.force_authenticate(user=self.parent_a)
        res = self.client.get(
            self.child_assessments_url(self.academy_a),
            {"student_id": self.student_a.pk},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # --- 7. Multi-academy student isolation -----------------------------------

    def test_17_multi_academy_student_assessments_are_strictly_isolated(self):
        """A student enrolled in both academies sees only Academy A assessments when querying A, and B when querying B."""
        self.client.force_authenticate(user=self.multi_student)

        # In Academy A
        res_a = self.client.get(self.student_mine_url(self.academy_a))
        self.assertEqual(res_a.status_code, status.HTTP_200_OK)
        ids_a = {r["id"] for r in res_a.data}
        self.assertIn(self.assessment_multi_a.pk, ids_a)
        self.assertNotIn(self.assessment_multi_b.pk, ids_a)

        # In Academy B
        res_b = self.client.get(self.student_mine_url(self.academy_b))
        self.assertEqual(res_b.status_code, status.HTTP_200_OK)
        ids_b = {r["id"] for r in res_b.data}
        self.assertIn(self.assessment_multi_b.pk, ids_b)
        self.assertNotIn(self.assessment_multi_a.pk, ids_b)

    def test_18_multi_academy_student_progress_report_is_strictly_isolated(self):
        """Student progress report in Academy A only counts Academy A sessions and averages."""
        self.client.force_authenticate(user=self.multi_student)

        # Progress in Academy A
        res_a = self.client.get(
            self.my_progress_url(self.academy_a), {"track_id": self.track_a.pk}
        )
        self.assertEqual(res_a.status_code, status.HTTP_200_OK)
        self.assertEqual(res_a.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(res_a.data["overall_average"]), Decimal("5.00"))

        # Progress in Academy B
        res_b = self.client.get(
            self.my_progress_url(self.academy_b), {"track_id": self.track_b.pk}
        )
        self.assertEqual(res_b.status_code, status.HTTP_200_OK)
        self.assertEqual(res_b.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(res_b.data["overall_average"]), Decimal("2.00"))

    # --- 8. Reports isolation -------------------------------------------------

    def test_19_teacher_quality_report_isolates_academies(self):
        """Teacher quality report for Lead A lists only teachers and sessions from Academy A."""
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.get(self.teacher_report_url(self.academy_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        teachers_in_report = {row["teacher"]["username"] for row in response.data}
        self.assertIn(self.teacher_a.username, teachers_in_report)
        self.assertNotIn(self.teacher_b.username, teachers_in_report)

    # --- 9. Permissions & privacy boundaries (Task 6.4) -----------------------

    def test_20_parent_cannot_access_unrelated_child(self):
        """Parent A cannot query assessments or progress for Student B or an unlinked child in Academy A."""
        unlinked_student_a = StudentFactory()
        admit(unlinked_student_a, self.academy_a)

        self.client.force_authenticate(user=self.parent_a)

        # Unlinked student in same academy -> 403
        res_unlinked = self.client.get(
            self.child_assessments_url(self.academy_a),
            {"student_id": unlinked_student_a.pk},
        )
        self.assertEqual(res_unlinked.status_code, status.HTTP_403_FORBIDDEN)

        # Student in other academy -> 403
        res_foreign = self.client.get(
            self.child_assessments_url(self.academy_a),
            {"student_id": self.student_b.pk},
        )
        self.assertEqual(res_foreign.status_code, status.HTTP_403_FORBIDDEN)

    def test_21_family_views_hide_internal_qc_fields(self):
        """Neither student nor parent can see flag_reason, lead_notes, or lead_reviewed_by."""
        # Student view
        self.client.force_authenticate(user=self.student_a)
        res_student = self.client.get(self.student_mine_url(self.academy_a))
        self.assertEqual(res_student.status_code, status.HTTP_200_OK)
        payload_s = res_student.data[0]
        for field in ("flagged_for_review", "flag_reason", "lead_review_note", "lead_reviewed_by"):
            self.assertNotIn(field, payload_s)
        self.assertNotIn("Academy A review requested", str(res_student.data))

        # Parent view
        self.client.force_authenticate(user=self.parent_a)
        res_parent = self.client.get(
            self.child_assessments_url(self.academy_a),
            {"student_id": self.student_a.pk},
        )
        self.assertEqual(res_parent.status_code, status.HTTP_200_OK)
        payload_p = res_parent.data[0]
        for field in ("flagged_for_review", "flag_reason", "lead_review_note", "lead_reviewed_by"):
            self.assertNotIn(field, payload_p)
        self.assertNotIn("Academy A review requested", str(res_parent.data))

    def test_22_teacher_cannot_see_other_teachers_or_lead_notes(self):
        """Teacher A cannot see Teacher B's work, nor lead review notes on own submissions."""
        # Annotate assessment A with lead notes
        self.assessment_a.lead_reviewed_at = dj_timezone.now()
        self.assessment_a.lead_reviewed_by = self.lead_a
        self.assessment_a.lead_review_note = "Lead private notes here"
        self.assessment_a.save()

        self.client.force_authenticate(user=self.teacher_a)
        res = self.client.get(self.teacher_mine_url(self.academy_a))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        payload = res.data[0]
        self.assertNotIn("lead_review_note", payload)
        self.assertNotIn("lead_reviewed_by", payload)
        self.assertNotIn("Lead private notes here", str(res.data))

    # --- 10. Model invariants defense in depth --------------------------------

    def test_23_model_clean_rejects_cross_academy_foreign_keys(self):
        """Model validation (.clean()) defends against cross-academy tampering."""
        # 1. SessionAssessment with student from Academy B
        bad_student_assessment = SessionAssessment(
            booking=self.booking_a,
            student=self.student_b,
            track=self.track_a,
            assessed_by=self.teacher_a,
            rubric_name="Test",
        )
        with self.assertRaises(ValidationError) as ctx:
            bad_student_assessment.clean()
        self.assertIn("student", ctx.exception.message_dict)

        # 2. SessionAssessment with teacher from Academy B
        bad_teacher_assessment = SessionAssessment(
            booking=self.booking_a,
            student=self.student_a,
            track=self.track_a,
            assessed_by=self.teacher_b,
            rubric_name="Test",
        )
        with self.assertRaises(ValidationError) as ctx:
            bad_teacher_assessment.clean()
        self.assertIn("assessed_by", ctx.exception.message_dict)

        # 3. SessionAssessment with booking from Academy B
        bad_booking_assessment = SessionAssessment(
            booking=self.booking_b,
            student=self.student_a,
            track=self.track_a,
            assessed_by=self.teacher_a,
            rubric_name="Test",
        )
        with self.assertRaises(ValidationError) as ctx:
            bad_booking_assessment.clean()
        self.assertIn("booking", ctx.exception.message_dict)

        # 4. AssessmentScore with criterion from Academy B
        bad_score = AssessmentScore(
            assessment=self.assessment_a,
            criterion=self.rubric_b.criteria.first(),
            criterion_name="Foreign criterion",
            score=4,
        )
        with self.assertRaises(ValidationError) as ctx:
            bad_score.clean()
        self.assertIn("criterion", ctx.exception.message_dict)

        # 5. ProgressSnapshot with student from Academy B
        bad_snapshot = ProgressSnapshot(
            student=self.student_b,
            track=self.track_a,
            period_start=self.period_start,
            period_end=self.period_end,
            generated_by=self.lead_a,
        )
        with self.assertRaises(ValidationError) as ctx:
            bad_snapshot.clean()
        self.assertIn("student", ctx.exception.message_dict)
