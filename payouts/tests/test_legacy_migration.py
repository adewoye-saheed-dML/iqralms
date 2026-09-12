"""Legacy payout data audit, remediation, and migration tests (Task 7.6).

Covers:
1. `LegacyPayoutRemediationTests`: Unit tests for `audit_payout_data` and
   `remediate_legacy_payouts` in `payouts.legacy`.
2. `LegacyPayoutMigrationTests`: Real database migration test running forward from
   `0001_initial` through `0002_remediate_legacy_payouts`.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone as dj_timezone

from accounts.models import OrganizationTeacherConfiguration, Role, User
from accounts.tests.factories import (
    LeadTeacherFactory,
    StudentFactory,
    SubTeacherFactory,
    TeacherProfileFactory,
)
from curriculum.tests.factories import GroupEligibleLevelFactory, LevelFactory, TrackFactory, admit
from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
)
from organizations.tests.factories import OrganizationFactory
from payouts.legacy import (
    LEGACY_TEACHER_ROLE,
    audit_payout_data,
    remediate_legacy_payouts,
)
from payouts.models import TeacherPayout
from payouts.tests.factories import TeacherPayoutFactory, past_session
from scheduling.models import Cohort
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableTeacherFactory,
    CohortFactory,
    ensure_teacher_configured,
    teaches,
)


class LegacyPayoutRemediationTests(TestCase):
    """Unit tests for audit and remediation functions in payouts.legacy."""

    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(name="Remediation Academy")
        self.track = TrackFactory(organization=self.org, name="Quran", slug="quran")
        self.level = LevelFactory(track=self.track, order=1)

        self.lead = LeadTeacherFactory()
        admit(self.lead, self.org, role=OrganizationRole.OWNER)
        ensure_teacher_configured(self.lead, self.org)

        self.teacher = BookableTeacherFactory()
        self.teacher.teacher_profile.hourly_payout_rate = Decimal("4000.00")
        self.teacher.teacher_profile.save()
        admit(self.teacher, self.org, role=OrganizationRole.TEACHER)
        ensure_teacher_configured(self.teacher, self.org)

        self.student = StudentFactory()
        admit(self.student, self.org, role=OrganizationRole.STAFF)

        self.window = AvailabilityFactory(
            organization=self.org, teacher=self.teacher
        )
        teaches(self.teacher, self.level)
        self.booking = past_session(
            availability=self.window,
            level=self.level,
            teacher=self.teacher,
            student=self.student,
            duration_minutes=60,
        )

    def test_audit_empty_database_has_no_anomalies(self):
        report = audit_payout_data(
            TeacherPayout=TeacherPayout,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertFalse(report.has_anomalies)

    def test_audit_clean_data_has_no_anomalies(self):
        TeacherPayoutFactory(booking=self.booking)

        report = audit_payout_data(
            TeacherPayout=TeacherPayout,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertFalse(report.has_anomalies)
        self.assertEqual(report.total_payouts, 1)

    def test_audit_flags_unadmitted_teacher(self):
        payout = TeacherPayoutFactory(booking=self.booking)
        # Remove teacher's membership
        OrganizationMembership.objects.filter(
            organization=self.org, user=self.teacher
        ).delete()

        report = audit_payout_data(
            TeacherPayout=TeacherPayout,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertTrue(report.has_anomalies)
        self.assertEqual(len(report.unadmitted_teachers), 1)
        self.assertEqual(report.unadmitted_teachers[0], (payout.pk, self.teacher.pk, self.org.pk))

    def test_audit_flags_suspended_teacher(self):
        payout = TeacherPayoutFactory(booking=self.booking)
        m = OrganizationMembership.objects.get(
            organization=self.org, user=self.teacher
        )
        m.status = MembershipStatus.SUSPENDED
        m.save()

        report = audit_payout_data(
            TeacherPayout=TeacherPayout,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertTrue(report.has_anomalies)
        self.assertEqual(len(report.suspended_teachers), 1)

    def test_audit_flags_unconfigured_teacher(self):
        payout = TeacherPayoutFactory(booking=self.booking)
        m = OrganizationMembership.objects.get(
            organization=self.org, user=self.teacher
        )
        OrganizationTeacherConfiguration.objects.filter(membership=m).delete()

        report = audit_payout_data(
            TeacherPayout=TeacherPayout,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )
        self.assertTrue(report.has_anomalies)
        self.assertEqual(len(report.unconfigured_teachers), 1)

    def test_remediate_backfills_unadmitted_teacher_and_configuration(self):
        payout = TeacherPayoutFactory(booking=self.booking)
        OrganizationTeacherConfiguration.objects.filter(
            membership__organization=self.org, membership__user=self.teacher
        ).delete()
        OrganizationMembership.objects.filter(
            organization=self.org, user=self.teacher
        ).delete()

        summary = remediate_legacy_payouts(
            TeacherPayout=TeacherPayout,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )

        self.assertEqual(summary["teachers_admitted"], 1)
        self.assertEqual(summary["teachers_configured"], 1)

        m = OrganizationMembership.objects.get(
            organization=self.org, user=self.teacher
        )
        self.assertEqual(m.role, LEGACY_TEACHER_ROLE)
        self.assertEqual(m.status, MembershipStatus.ACTIVE)
        self.assertTrue(
            OrganizationTeacherConfiguration.objects.filter(membership=m).exists()
        )

    def test_remediate_does_not_reactivate_suspended_membership(self):
        payout = TeacherPayoutFactory(booking=self.booking)
        m = OrganizationMembership.objects.get(
            organization=self.org, user=self.teacher
        )
        m.status = MembershipStatus.SUSPENDED
        m.save()

        summary = remediate_legacy_payouts(
            TeacherPayout=TeacherPayout,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )

        self.assertEqual(summary["teachers_admitted"], 0)
        m.refresh_from_db()
        self.assertEqual(m.status, MembershipStatus.SUSPENDED)

    def test_remediate_aligns_cohort_mismatch(self):
        group_level = GroupEligibleLevelFactory(track=self.track)
        start = dj_timezone.now() - timedelta(weeks=1)
        cohort = CohortFactory(
            level=group_level,
            teacher=self.teacher,
            availability=self.window,
            schedule_start_utc=start,
        )
        teaches(self.teacher, group_level)
        cohort_booking = past_session(
            availability=self.window,
            level=group_level,
            teacher=self.teacher,
            student=self.student,
            cohort=cohort,
            duration_minutes=60,
            start_time_utc=start,
        )
        # Create payout where cohort was omitted or mismatched
        payout = TeacherPayoutFactory(booking=cohort_booking)
        # Deliberately desynchronize cohort in database
        TeacherPayout.objects.filter(pk=payout.pk).update(cohort=None)
        payout.refresh_from_db()
        self.assertIsNone(payout.cohort)

        summary = remediate_legacy_payouts(
            TeacherPayout=TeacherPayout,
            OrganizationMembership=OrganizationMembership,
            OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
        )

        self.assertEqual(summary["cohorts_aligned"], 1)
        payout.refresh_from_db()
        self.assertEqual(payout.cohort_id, cohort.pk)


class LegacyPayoutMigrationTests(TransactionTestCase):
    """Real database migration forward execution test."""

    migrate_from = [("payouts", "0001_initial")]
    migrate_to = [("payouts", "0002_remediate_legacy_payouts")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_migration_runs_cleanly(self):
        executor = MigrationExecutor(connection)
        # Migrate to initial
        executor.migrate(self.migrate_from)

        # Migrate forward
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
