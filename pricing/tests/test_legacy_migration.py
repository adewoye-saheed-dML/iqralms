"""Legacy pricing data audit, remediation, and migration tests (Task 5.6).

Two layers:
1. `LegacyPricingRemediationTests`: Unit tests for `audit_pricing_agreements` and
   `remediate_legacy_pricing` in `pricing.legacy`.
2. `LegacyPricingMigrationTests`: Real database migration test running forward from
   `0001_initial` through `0002_remediate_legacy_pricing`.
"""

from decimal import Decimal
from unittest.mock import MagicMock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from accounts.models import Role, User
from accounts.tests.factories import LeadTeacherFactory, StudentFactory
from curriculum.models import Level, Track
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
)
from organizations.tests.factories import OrganizationFactory
from pricing.legacy import (
    LEGACY_APPROVER_ROLE,
    LEGACY_STUDENT_ROLE,
    audit_pricing_agreements,
    remediate_legacy_pricing,
)
from pricing.models import PricingAgreement, PricingReason
from pricing.tests.factories import HARDSHIP_RATE, PREMIUM_RATE, STANDARD_RATE


class LegacyPricingRemediationTests(TestCase):
    """Unit tests for the audit and remediation routines."""

    def setUp(self):
        super().setUp()
        self.org = OrganizationFactory(name="Audit Academy")
        self.track = TrackFactory(organization=self.org, name="Hifz", slug="hifz")
        self.level = LevelFactory(track=self.track)
        self.lead = LeadTeacherFactory()
        self.student = StudentFactory()

    def test_audit_empty_database_has_no_anomalies(self):
        report = audit_pricing_agreements(
            PricingAgreement=PricingAgreement,
            OrganizationMembership=OrganizationMembership,
        )
        self.assertEqual(report.total_agreements, 0)
        self.assertFalse(report.has_anomalies)

    def test_audit_valid_agreements_has_no_anomalies(self):
        admit(self.lead, self.org, role=OrganizationRole.OWNER)
        admit(self.student, self.org, role=OrganizationRole.STAFF)

        PricingAgreement.objects.create(
            student=self.student,
            level=self.level,
            approved_by=self.lead,
            standard_rate=STANDARD_RATE,
            agreed_rate=HARDSHIP_RATE,
            reason=PricingReason.DISCOUNT_HARDSHIP,
            active=True,
        )

        report = audit_pricing_agreements(
            PricingAgreement=PricingAgreement,
            OrganizationMembership=OrganizationMembership,
        )
        self.assertEqual(report.total_agreements, 1)
        self.assertFalse(report.has_anomalies)

    def test_audit_detects_unadmitted_student_and_approver(self):
        # Neither student nor lead has membership
        agreements = PricingAgreement.objects.bulk_create(
            [
                PricingAgreement(
                    student=self.student,
                    level=self.level,
                    approved_by=self.lead,
                    standard_rate=STANDARD_RATE,
                    agreed_rate=HARDSHIP_RATE,
                    reason=PricingReason.DISCOUNT_HARDSHIP,
                    active=True,
                )
            ]
        )
        agreement = agreements[0]

        report = audit_pricing_agreements(
            PricingAgreement=PricingAgreement,
            OrganizationMembership=OrganizationMembership,
        )
        self.assertTrue(report.has_anomalies)
        self.assertEqual(len(report.unadmitted_students), 1)
        self.assertEqual(
            report.unadmitted_students[0],
            (agreement.pk, self.student.pk, self.org.pk),
        )
        self.assertEqual(len(report.unadmitted_approvers), 1)
        self.assertEqual(
            report.unadmitted_approvers[0],
            (agreement.pk, self.lead.pk, self.org.pk),
        )

    def test_audit_detects_suspended_members(self):
        lead_membership = admit(self.lead, self.org, role=OrganizationRole.OWNER)
        student_membership = admit(self.student, self.org, role=OrganizationRole.STAFF)
        lead_membership.status = MembershipStatus.SUSPENDED
        lead_membership.save()
        student_membership.status = MembershipStatus.SUSPENDED
        student_membership.save()

        PricingAgreement.objects.bulk_create(
            [
                PricingAgreement(
                    student=self.student,
                    level=self.level,
                    approved_by=self.lead,
                    standard_rate=STANDARD_RATE,
                    agreed_rate=HARDSHIP_RATE,
                    reason=PricingReason.DISCOUNT_HARDSHIP,
                    active=True,
                )
            ]
        )

        report = audit_pricing_agreements(
            PricingAgreement=PricingAgreement,
            OrganizationMembership=OrganizationMembership,
        )
        self.assertTrue(report.has_anomalies)
        self.assertEqual(len(report.suspended_students), 1)
        self.assertEqual(len(report.suspended_approvers), 1)

    def test_audit_detects_duplicate_active_agreements(self):
        admit(self.lead, self.org, role=OrganizationRole.OWNER)
        admit(self.student, self.org, role=OrganizationRole.STAFF)

        # Mock agreements to test audit algorithm for duplicate active rows
        # independent of the database partial unique constraint
        agr1 = MagicMock(
            pk=101,
            student_id=self.student.pk,
            level_id=self.level.pk,
            approved_by_id=self.lead.pk,
            active=True,
            level=self.level,
        )
        agr2 = MagicMock(
            pk=102,
            student_id=self.student.pk,
            level_id=self.level.pk,
            approved_by_id=self.lead.pk,
            active=True,
            level=self.level,
        )
        mock_qs = MagicMock()
        mock_qs.__iter__.return_value = iter([agr2, agr1])
        mock_qs.count.return_value = 2
        MockAgreement = MagicMock()
        MockAgreement.objects.all.return_value.select_related.return_value.order_by.return_value = (
            mock_qs
        )

        report = audit_pricing_agreements(
            PricingAgreement=MockAgreement,
            OrganizationMembership=OrganizationMembership,
        )
        self.assertTrue(report.has_anomalies)
        self.assertEqual(len(report.duplicate_active_groups), 1)
        self.assertIn(
            (self.student.pk, self.level.pk), report.duplicate_active_groups
        )
        self.assertEqual(
            set(report.duplicate_active_groups[(self.student.pk, self.level.pk)]),
            {101, 102},
        )

    def test_remediate_backfills_memberships_without_altering_suspended(self):
        # 1 unadmitted student, 1 unadmitted approver
        student_unadmitted = StudentFactory()
        lead_unadmitted = LeadTeacherFactory()

        # 1 suspended student (should stay suspended)
        student_suspended = StudentFactory()
        admit(student_suspended, self.org, role=OrganizationRole.STAFF)
        OrganizationMembership.objects.filter(
            organization=self.org, user=student_suspended
        ).update(status=MembershipStatus.SUSPENDED)

        PricingAgreement.objects.bulk_create(
            [
                PricingAgreement(
                    student=student_unadmitted,
                    level=self.level,
                    approved_by=lead_unadmitted,
                    standard_rate=STANDARD_RATE,
                    agreed_rate=HARDSHIP_RATE,
                    reason=PricingReason.DISCOUNT_HARDSHIP,
                    active=True,
                ),
                PricingAgreement(
                    student=student_suspended,
                    level=self.level,
                    approved_by=lead_unadmitted,
                    standard_rate=STANDARD_RATE,
                    agreed_rate=HARDSHIP_RATE,
                    reason=PricingReason.DISCOUNT_HARDSHIP,
                    active=True,
                ),
            ]
        )

        summary = remediate_legacy_pricing(
            PricingAgreement=PricingAgreement,
            OrganizationMembership=OrganizationMembership,
        )
        self.assertEqual(summary["students_admitted"], 1)
        self.assertEqual(summary["approvers_admitted"], 1)

        # Check admitted student
        student_membership = OrganizationMembership.objects.get(
            organization=self.org, user=student_unadmitted
        )
        self.assertEqual(student_membership.role, LEGACY_STUDENT_ROLE)
        self.assertEqual(student_membership.status, MembershipStatus.ACTIVE)

        # Check admitted approver
        lead_membership = OrganizationMembership.objects.get(
            organization=self.org, user=lead_unadmitted
        )
        self.assertEqual(lead_membership.role, LEGACY_APPROVER_ROLE)
        self.assertEqual(lead_membership.status, MembershipStatus.ACTIVE)

        # Check suspended student was NOT touched
        susp_mem = OrganizationMembership.objects.get(
            organization=self.org, user=student_suspended
        )
        self.assertEqual(susp_mem.status, MembershipStatus.SUSPENDED)

    def test_remediate_deactivates_older_duplicates_non_destructively(self):
        agr1 = MagicMock(
            pk=101,
            student_id=self.student.pk,
            level_id=self.level.pk,
            approved_by_id=self.lead.pk,
            active=True,
            level=self.level,
        )
        agr2 = MagicMock(
            pk=102,
            student_id=self.student.pk,
            level_id=self.level.pk,
            approved_by_id=self.lead.pk,
            active=True,
            level=self.level,
        )
        MockAgreement = MagicMock()
        MockAgreement.objects.all.return_value.select_related.return_value.order_by.return_value = [
            agr2,
            agr1,
        ]

        summary = remediate_legacy_pricing(
            PricingAgreement=MockAgreement,
            OrganizationMembership=OrganizationMembership,
        )
        self.assertEqual(summary["duplicate_agreements_deactivated"], 1)
        self.assertTrue(agr2.active)
        self.assertFalse(agr1.active)
        MockAgreement.objects.bulk_update.assert_called_once_with([agr1], ["active"])


class LegacyPricingMigrationTests(TransactionTestCase):
    """Database migration test executing 0002_remediate_legacy_pricing."""

    BEFORE = [
        ("pricing", "0001_initial"),
        ("curriculum", "0004_enforce_curriculum_tenancy"),
        ("organizations", "0001_initial"),
    ]
    AFTER = [("pricing", "0002_remediate_legacy_pricing")]

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
        executor.migrate(executor.loader.graph.leaf_nodes())
        executor.loader.build_graph()

    def test_migration_remediates_legacy_pricing_data(self):
        OldOrg = self.old_apps.get_model("organizations", "Organization")
        OldTrack = self.old_apps.get_model("curriculum", "Track")
        OldLevel = self.old_apps.get_model("curriculum", "Level")
        OldUser = self.old_apps.get_model("accounts", "User")
        OldAgreement = self.old_apps.get_model("pricing", "PricingAgreement")
        OldMembership = self.old_apps.get_model(
            "organizations", "OrganizationMembership"
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
        lead = OldUser.objects.create(
            username="legacy_lead",
            email="legacy_lead@example.com",
            role=Role.LEAD,
            timezone="UTC",
            signup_code="LEGLEA01",
            password="x",
        )

        # Neither user has an OrganizationMembership yet.
        agr1 = OldAgreement.objects.create(
            student=student,
            level=level,
            approved_by=lead,
            standard_rate=Decimal("30.00"),
            agreed_rate=Decimal("20.00"),
            reason=PricingReason.DISCOUNT_HARDSHIP,
            active=True,
        )

        # Run migration forward to 0002_remediate_legacy_pricing
        self.fast_forward()

        # Verify post-migration state
        executor = MigrationExecutor(connection)
        new_apps = executor.loader.project_state(self.AFTER).apps
        NewAgreement = new_apps.get_model("pricing", "PricingAgreement")
        NewMembership = new_apps.get_model(
            "organizations", "OrganizationMembership"
        )

        # 1. Memberships were created
        self.assertTrue(
            NewMembership.objects.filter(
                organization_id=org.pk,
                user_id=student.pk,
                role=LEGACY_STUDENT_ROLE,
                status=MembershipStatus.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            NewMembership.objects.filter(
                organization_id=org.pk,
                user_id=lead.pk,
                role=LEGACY_APPROVER_ROLE,
                status=MembershipStatus.ACTIVE,
            ).exists()
        )

        # 2. Agreement remains intact and active
        self.assertEqual(NewAgreement.objects.count(), 1)
        row1 = NewAgreement.objects.get(pk=agr1.pk)
        self.assertTrue(row1.active)
