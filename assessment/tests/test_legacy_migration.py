"""Legacy assessment data audit, remediation, and migration tests (Task 6.6).

Two layers:
1. `LegacyAssessmentRemediationTests`: Unit tests for `audit_assessment_data` and
   `remediate_legacy_assessment` in `assessment.legacy`.
2. `LegacyAssessmentMigrationTests`: Real database migration test running forward from
   `0001_initial` through `0002_remediate_legacy_assessment`.
"""

from datetime import timedelta
from unittest.mock import MagicMock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone as dj_timezone

from accounts.models import OrganizationTeacherConfiguration, Role, User
from accounts.tests.factories import (
    LeadTeacherFactory,
    StudentFactory,
)
from assessment.legacy import (
    LEGACY_LEAD_ROLE,
    LEGACY_STUDENT_ROLE,
    LEGACY_TEACHER_ROLE,
    audit_assessment_data,
    remediate_legacy_assessment,
)
from assessment.models import (
    AssessmentCriterion,
    AssessmentRubric,
    AssessmentScore,
    ProgressSnapshot,
    SessionAssessment,
)
from assessment.tests.factories import (
    AssessmentRubricFactory,
    RubricWithCriteriaFactory,
    SessionAssessmentFactory,
    past_completed_booking,
)
from curriculum.models import Level, Track
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
)
from organizations.tests.factories import OrganizationFactory
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableTeacherFactory,
    ensure_teacher_configured,
)


class LegacyAssessmentRemediationTests(TestCase):
    """Unit tests for audit and remediation functions in assessment.legacy."""

    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(name="Remediation Academy")
        self.track = TrackFactory(organization=self.org, name="Quran", slug="quran")
        self.level = LevelFactory(track=self.track, order=1)
        self.rubric = RubricWithCriteriaFactory(track=self.track, name="Rubric")

        self.lead = LeadTeacherFactory()
        admit(self.lead, self.org, role=OrganizationRole.OWNER)
        ensure_teacher_configured(self.lead, self.org)

        self.teacher = BookableTeacherFactory()
        admit(self.teacher, self.org, role=OrganizationRole.TEACHER)
        ensure_teacher_configured(self.teacher, self.org)

        self.student = StudentFactory()
        admit(self.student, self.org, role=OrganizationRole.STAFF)

        self.window = AvailabilityFactory(
            organization=self.org, teacher=self.teacher
        )
        self.booking = past_completed_booking(
            availability=self.window,
            level=self.level,
            student=self.student,
            weeks_ago=1,
        )

    def test_audit_empty_database_has_no_anomalies(self):
        report = audit_assessment_data(
            AssessmentRubric=AssessmentRubric,
            SessionAssessment=SessionAssessment,
            AssessmentScore=AssessmentScore,
            ProgressSnapshot=ProgressSnapshot,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertFalse(report.has_anomalies)

    def test_audit_clean_data_has_no_anomalies(self):
        SessionAssessmentFactory(booking=self.booking)
        ProgressSnapshot.objects.bulk_create(
            [
                ProgressSnapshot(
                    student=self.student,
                    track=self.track,
                    period_start=dj_timezone.now() - timedelta(weeks=2),
                    period_end=dj_timezone.now(),
                    generated_by=self.lead,
                    completed_sessions=1,
                    assessed_sessions=1,
                    overall_average=4.0,
                )
            ]
        )

        report = audit_assessment_data(
            AssessmentRubric=AssessmentRubric,
            SessionAssessment=SessionAssessment,
            AssessmentScore=AssessmentScore,
            ProgressSnapshot=ProgressSnapshot,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertFalse(report.has_anomalies)

    def test_audit_detects_unadmitted_participants(self):
        # Create unadmitted student and teacher
        stranger_student = StudentFactory()
        stranger_teacher = BookableTeacherFactory()

        # Simulate legacy rows created before validation
        assessments = SessionAssessment.objects.bulk_create(
            [
                SessionAssessment(
                    booking=self.booking,
                    student=stranger_student,
                    track=self.track,
                    assessed_by=stranger_teacher,
                    rubric_name="Sheet",
                )
            ]
        )
        assessment = assessments[0]

        report = audit_assessment_data(
            AssessmentRubric=AssessmentRubric,
            SessionAssessment=SessionAssessment,
            AssessmentScore=AssessmentScore,
            ProgressSnapshot=ProgressSnapshot,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertTrue(report.has_anomalies)
        self.assertEqual(len(report.unadmitted_students), 1)
        self.assertEqual(
            report.unadmitted_students[0],
            (assessment.pk, stranger_student.pk, self.org.pk),
        )
        self.assertEqual(len(report.unadmitted_teachers), 1)
        self.assertEqual(
            report.unadmitted_teachers[0],
            (assessment.pk, stranger_teacher.pk, self.org.pk),
        )

    def test_audit_detects_suspended_members(self):
        assessment = SessionAssessmentFactory(booking=self.booking)

        # Suspend student membership
        m_student = OrganizationMembership.objects.get(
            organization=self.org, user=self.student
        )
        m_student.status = MembershipStatus.SUSPENDED
        m_student.save()

        # Suspend teacher membership
        m_teacher = OrganizationMembership.objects.get(
            organization=self.org, user=self.teacher
        )
        m_teacher.status = MembershipStatus.SUSPENDED
        m_teacher.save()

        report = audit_assessment_data(
            AssessmentRubric=AssessmentRubric,
            SessionAssessment=SessionAssessment,
            AssessmentScore=AssessmentScore,
            ProgressSnapshot=ProgressSnapshot,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertTrue(report.has_anomalies)
        self.assertEqual(len(report.suspended_students), 1)
        self.assertEqual(len(report.suspended_teachers), 1)

    def test_audit_detects_mismatched_track_and_cross_org_scores(self):
        assessment = SessionAssessmentFactory(booking=self.booking)

        # Create another organization and rubric
        other_org = OrganizationFactory(name="Other Academy")
        other_track = TrackFactory(organization=other_org, name="Other", slug="other")
        other_rubric = RubricWithCriteriaFactory(track=other_track)

        # Update directly via SQL to simulate pre-existing database anomaly
        SessionAssessment.objects.filter(pk=assessment.pk).update(track=other_track)

        # Cross-org score
        score = assessment.scores.first()
        AssessmentScore.objects.filter(pk=score.pk).update(
            criterion=other_rubric.criteria.first()
        )

        report = audit_assessment_data(
            AssessmentRubric=AssessmentRubric,
            SessionAssessment=SessionAssessment,
            AssessmentScore=AssessmentScore,
            ProgressSnapshot=ProgressSnapshot,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertTrue(report.has_anomalies)
        self.assertIn(assessment.pk, report.mismatched_tracks)
        self.assertIn(score.pk, report.cross_org_scores)

    def test_remediate_backfills_memberships_and_repairs_tracks(self):
        new_student = StudentFactory()
        new_teacher = BookableTeacherFactory()
        new_reviewer = LeadTeacherFactory()

        # Simulate legacy assessment with mismatched track and unadmitted participants
        wrong_track = TrackFactory(organization=self.org, name="Wrong", slug="wrong")
        assessments = SessionAssessment.objects.bulk_create(
            [
                SessionAssessment(
                    booking=self.booking,
                    student=new_student,
                    track=wrong_track,
                    assessed_by=new_teacher,
                    lead_reviewed_by=new_reviewer,
                    rubric_name="Test",
                )
            ]
        )
        assessment = assessments[0]

        ProgressSnapshot.objects.bulk_create(
            [
                ProgressSnapshot(
                    student=new_student,
                    track=self.track,
                    period_start=dj_timezone.now() - timedelta(weeks=2),
                    period_end=dj_timezone.now(),
                    generated_by=new_reviewer,
                    completed_sessions=1,
                    assessed_sessions=0,
                )
            ]
        )

        summary = remediate_legacy_assessment(
            SessionAssessment=SessionAssessment,
            ProgressSnapshot=ProgressSnapshot,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )

        self.assertEqual(summary["students_admitted"], 1)
        self.assertEqual(summary["teachers_admitted"], 1)
        self.assertEqual(summary["teachers_configured"], 1)
        self.assertEqual(summary["reviewers_admitted"], 1)
        self.assertEqual(summary["tracks_repaired"], 1)

        # Verify track was corrected on assessment
        assessment.refresh_from_db()
        self.assertEqual(assessment.track, self.booking.level.track)

        # Verify active memberships exist
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization=self.org,
                user=new_student,
                role=LEGACY_STUDENT_ROLE,
                status=MembershipStatus.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization=self.org,
                user=new_teacher,
                role=LEGACY_TEACHER_ROLE,
                status=MembershipStatus.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            OrganizationTeacherConfiguration.objects.filter(
                membership__user=new_teacher, approved=True
            ).exists()
        )
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization=self.org,
                user=new_reviewer,
                role=LEGACY_LEAD_ROLE,
                status=MembershipStatus.ACTIVE,
            ).exists()
        )

    def test_remediate_never_reactivates_suspended_membership(self):
        # A suspended member is not reactivated
        m = OrganizationMembership.objects.get(
            organization=self.org, user=self.student
        )
        m.status = MembershipStatus.SUSPENDED
        m.save()

        SessionAssessment.objects.bulk_create(
            [
                SessionAssessment(
                    booking=self.booking,
                    student=self.student,
                    track=self.track,
                    assessed_by=self.teacher,
                    rubric_name="Test",
                )
            ]
        )

        summary = remediate_legacy_assessment(
            SessionAssessment=SessionAssessment,
            ProgressSnapshot=ProgressSnapshot,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertEqual(summary["students_admitted"], 0)
        m.refresh_from_db()
        self.assertEqual(m.status, MembershipStatus.SUSPENDED)


class LegacyAssessmentMigrationTests(TransactionTestCase):
    """Database migration test executing 0002_remediate_legacy_assessment."""

    BEFORE = [
        ("assessment", "0001_initial"),
        ("curriculum", "0004_enforce_curriculum_tenancy"),
        ("organizations", "0001_initial"),
        ("accounts", "0003_organizationteacherconfiguration"),
        ("scheduling", "0008_enforce_availability_tenancy"),
    ]
    AFTER = [("assessment", "0002_remediate_legacy_assessment")]

    def setUp(self):
        super().setUp()
        self.rewind()

    def tearDown(self):
        self.fast_forward()
        super().tearDown()

    def rewind(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.BEFORE)
        executor.loader.build_graph()
        self.old_apps = executor.loader.project_state(self.BEFORE).apps

    def fast_forward(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.AFTER)
        executor.loader.build_graph()

    def test_migration_remediates_legacy_assessment_data(self):
        OldOrg = self.old_apps.get_model("organizations", "Organization")
        OldTrack = self.old_apps.get_model("curriculum", "Track")
        OldLevel = self.old_apps.get_model("curriculum", "Level")
        OldUser = self.old_apps.get_model("accounts", "User")
        OldBooking = self.old_apps.get_model("scheduling", "Booking")
        OldAssessment = self.old_apps.get_model("assessment", "SessionAssessment")
        OldSnapshot = self.old_apps.get_model("assessment", "ProgressSnapshot")
        OldMembership = self.old_apps.get_model("organizations", "OrganizationMembership")
        OldTeacherConfig = self.old_apps.get_model(
            "accounts", "OrganizationTeacherConfiguration"
        )

        org = OldOrg.objects.create(
            name="Legacy Quran Academy",
            slug="legacy-quran",
            timezone="UTC",
            is_active=True,
        )
        track = OldTrack.objects.create(
            organization=org,
            name="Tajweed",
            slug="tajweed",
        )
        level = OldLevel.objects.create(track=track, order=1, name="Level 1")

        student = OldUser.objects.create(
            username="legacy_student",
            email="legacy_student@example.com",
            role=Role.STUDENT,
            timezone="UTC",
            signup_code="LEGSTU01",
            password="x",
        )
        teacher = OldUser.objects.create(
            username="legacy_teacher",
            email="legacy_teacher@example.com",
            role=Role.SUB,
            timezone="UTC",
            signup_code="LEGTEA01",
            password="x",
        )
        lead = OldUser.objects.create(
            username="legacy_lead",
            email="legacy_lead@example.com",
            role=Role.LEAD,
            timezone="UTC",
            signup_code="LEGLEA01",
            password="x",
        )

        # Create booking without memberships
        booking = OldBooking.objects.create(
            student=student,
            teacher=teacher,
            level=level,
            start_time_utc=dj_timezone.now() - timedelta(days=1),
            duration_minutes=30,
            status="completed",
        )

        assessment = OldAssessment.objects.create(
            booking=booking,
            student=student,
            track=track,
            assessed_by=teacher,
            rubric_name="Legacy Sheet",
        )

        snapshot = OldSnapshot.objects.create(
            student=student,
            track=track,
            period_start=dj_timezone.now() - timedelta(weeks=2),
            period_end=dj_timezone.now(),
            generated_by=lead,
            completed_sessions=1,
            assessed_sessions=1,
        )

        # Neither user has OrganizationMembership or OrganizationTeacherConfiguration yet
        self.assertFalse(OldMembership.objects.filter(organization=org).exists())

        # Fast forward migration
        self.fast_forward()

        # Re-fetch models from current schema
        NewMembership = connection.introspection.installed_models({"organizations", "accounts"})
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization_id=org.pk,
                user_id=student.pk,
                status=MembershipStatus.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization_id=org.pk,
                user_id=teacher.pk,
                status=MembershipStatus.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization_id=org.pk,
                user_id=lead.pk,
                status=MembershipStatus.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            OrganizationTeacherConfiguration.objects.filter(
                membership__organization_id=org.pk,
                membership__user_id=teacher.pk,
                approved=True,
            ).exists()
        )
