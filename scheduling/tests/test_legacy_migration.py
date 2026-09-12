"""The pre-SaaS availability backfill — the decision, and the migration itself.

Two layers, because they fail in different ways:

``LegacyOrganizationChoiceTests`` covers ``scheduling.legacy``'s judgement calls:
which academy owns availability that predates multi-tenancy. Getting that wrong
does not raise an error — it silently hands one tenant another's teacher hours — so
every branch is asserted, including both refusals.

``LegacyAvailabilityMigrationTests`` runs the real migrations against a real database
that starts in the pre-SaaS shape: availability windows with no owner, teachers
holding no membership yet, and migrating forward through the data migration.
"""

import os
from datetime import time
from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from accounts.models import Role
from curriculum.tests.factories import admit
from scheduling.tests.factories import AvailabilityFactory, BookableTeacherFactory
from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
)
from organizations.tests.factories import OrganizationFactory
from scheduling.legacy import (
    LEGACY_ORGANIZATION_ENV,
    LegacySchedulingUnmapped,
    admit_legacy_teachers,
    choose_legacy_organization_assignments,
    unowned_availability,
)
from scheduling.models import Availability, Weekday
from scheduling.tests.factories import AvailabilityFactory


class LegacyOrganizationChoiceTests(TestCase):
    """"Resolve or refuse" — never guess. One test per branch."""

    def test_nothing_to_own_means_nothing_to_do(self):
        """A fresh install, and every test database, takes this path."""
        OrganizationFactory()
        self.assertEqual(
            choose_legacy_organization_assignments(
                Availability=Availability,
                Organization=Organization,
                OrganizationMembership=OrganizationMembership,
                unowned_rows=[],
            ),
            {},
        )

    def test_one_organization_is_unambiguous(self):
        only = OrganizationFactory()
        teacher = BookableTeacherFactory()
        admit(teacher, only)
        window = AvailabilityFactory(teacher=teacher, organization=only)

        assignments = choose_legacy_organization_assignments(
            Availability=Availability,
            Organization=Organization,
            OrganizationMembership=OrganizationMembership,
            unowned_rows=[window],
        )
        self.assertEqual(assignments, {window.pk: only})

    def test_no_organization_refuses_rather_than_inventing_one(self):
        teacher = BookableTeacherFactory()
        org = OrganizationFactory()
        admit(teacher, org)
        window = AvailabilityFactory(teacher=teacher, organization=org)
        Organization.objects.all().delete()

        with self.assertRaises(LegacySchedulingUnmapped) as ctx:
            choose_legacy_organization_assignments(
                Availability=Availability,
                Organization=Organization,
                OrganizationMembership=OrganizationMembership,
                unowned_rows=[window],
            )
        self.assertIn(LEGACY_ORGANIZATION_ENV, str(ctx.exception))

    def test_two_organizations_refuse_if_teacher_belongs_to_both(self):
        org1 = OrganizationFactory()
        org2 = OrganizationFactory()
        teacher = BookableTeacherFactory()
        admit(teacher, org1)
        admit(teacher, org2)
        window = AvailabilityFactory(teacher=teacher, organization=org1)

        with self.assertRaises(LegacySchedulingUnmapped) as ctx:
            choose_legacy_organization_assignments(
                Availability=Availability,
                Organization=Organization,
                OrganizationMembership=OrganizationMembership,
                unowned_rows=[window],
            )
        self.assertIn(teacher.username, str(ctx.exception))
        self.assertIn(LEGACY_ORGANIZATION_ENV, str(ctx.exception))

    def test_two_organizations_refuse_if_teacher_has_no_organization(self):
        OrganizationFactory()
        OrganizationFactory()
        teacher = BookableTeacherFactory()
        org = OrganizationFactory()
        admit(teacher, org)
        window = AvailabilityFactory(teacher=teacher, organization=org)
        OrganizationMembership.objects.filter(user=teacher).delete()

        with self.assertRaises(LegacySchedulingUnmapped) as ctx:
            choose_legacy_organization_assignments(
                Availability=Availability,
                Organization=Organization,
                OrganizationMembership=OrganizationMembership,
                unowned_rows=[window],
            )
        self.assertIn(teacher.username, str(ctx.exception))
        self.assertIn(LEGACY_ORGANIZATION_ENV, str(ctx.exception))

    def test_two_organizations_resolves_when_teachers_belong_to_distinct_academies(self):
        org1 = OrganizationFactory(slug="academy-1")
        org2 = OrganizationFactory(slug="academy-2")
        teacher1 = BookableTeacherFactory()
        teacher2 = BookableTeacherFactory()
        admit(teacher1, org1)
        admit(teacher2, org2)

        w1 = AvailabilityFactory(teacher=teacher1, organization=org1)
        w2 = AvailabilityFactory(teacher=teacher2, organization=org2)

        assignments = choose_legacy_organization_assignments(
            Availability=Availability,
            Organization=Organization,
            OrganizationMembership=OrganizationMembership,
            unowned_rows=[w1, w2],
        )
        self.assertEqual(assignments, {w1.pk: org1, w2.pk: org2})

    def test_an_explicit_primary_key_settles_it(self):
        OrganizationFactory()
        wanted = OrganizationFactory()
        teacher = BookableTeacherFactory()
        admit(teacher, wanted)
        window = AvailabilityFactory(teacher=teacher, organization=wanted)

        assignments = choose_legacy_organization_assignments(
            Availability=Availability,
            Organization=Organization,
            OrganizationMembership=OrganizationMembership,
            named=str(wanted.pk),
            unowned_rows=[window],
        )
        self.assertEqual(assignments, {window.pk: wanted})

    def test_an_explicit_slug_settles_it_too(self):
        OrganizationFactory()
        wanted = OrganizationFactory(slug="al-furqan")
        teacher = BookableTeacherFactory()
        admit(teacher, wanted)
        window = AvailabilityFactory(teacher=teacher, organization=wanted)

        assignments = choose_legacy_organization_assignments(
            Availability=Availability,
            Organization=Organization,
            OrganizationMembership=OrganizationMembership,
            named="al-furqan",
            unowned_rows=[window],
        )
        self.assertEqual(assignments, {window.pk: wanted})

    def test_a_name_that_matches_nothing_is_refused(self):
        OrganizationFactory()
        teacher = BookableTeacherFactory()
        org = OrganizationFactory()
        admit(teacher, org)
        window = AvailabilityFactory(teacher=teacher, organization=org)

        with self.assertRaises(LegacySchedulingUnmapped):
            choose_legacy_organization_assignments(
                Availability=Availability,
                Organization=Organization,
                OrganizationMembership=OrganizationMembership,
                named="nonexistent-academy",
                unowned_rows=[window],
            )

    def test_suspended_membership_is_left_suspended(self):
        org = OrganizationFactory()
        teacher = BookableTeacherFactory()
        OrganizationMembership.objects.create(
            organization=org,
            user=teacher,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.SUSPENDED,
        )
        # Calling admit_legacy_teachers does not overwrite or activate
        created = admit_legacy_teachers(
            OrganizationMembership=OrganizationMembership,
            organization=org,
            teachers=[teacher],
        )
        self.assertEqual(created, 0)
        membership = OrganizationMembership.objects.get(organization=org, user=teacher)
        self.assertEqual(membership.status, MembershipStatus.SUSPENDED)


class LegacyAvailabilityMigrationTests(TransactionTestCase):
    """The migrations themselves, against a database in the pre-SaaS shape.

    ``TransactionTestCase`` because migrating runs DDL and commits, which a
    ``TestCase``'s wrapping transaction cannot contain.
    """

    BEFORE = [
        ("scheduling", "0006_availability_organization_ownership"),
        ("curriculum", "0004_enforce_curriculum_tenancy"),
        ("accounts", "0003_organizationteacherconfiguration"),
        ("organizations", "0001_initial"),
    ]
    AFTER = [("scheduling", "0008_enforce_availability_tenancy")]

    def setUp(self):
        self.rewind()

    def tearDown(self):
        self.fast_forward()

    def rewind(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.BEFORE)
        executor.loader.build_graph()
        self.old_apps = executor.loader.project_state(self.BEFORE).apps

    def fast_forward(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        # Migrate fully to the latest state of all apps
        executor.migrate(executor.loader.graph.leaf_nodes())
        executor.loader.build_graph()

    def old(self, app_label, model_name):
        return self.old_apps.get_model(app_label, model_name)

    def user(self, username, role):
        return self.old("accounts", "User").objects.create(
            username=username,
            email=f"{username}@example.com",
            role=role,
            timezone="Africa/Lagos",
            signup_code=username[:8].upper(),
            password="x",
        )

    def test_empty_availability_migrates_with_nothing_to_do(self):
        """Fresh install / test database."""
        self.fast_forward()
        AvailabilityModel = connection.introspection.table_names()
        self.assertIn("scheduling_availability", AvailabilityModel)

    def test_legacy_availability_is_assigned_and_admits_teacher(self):
        """Pre-SaaS unowned availability is assigned to single academy and admits teacher."""
        OldOrg = self.old("organizations", "Organization")
        OldMembership = self.old("organizations", "OrganizationMembership")
        OldAvailability = self.old("scheduling", "Availability")

        org = OldOrg.objects.create(
            name="Academy Al-Huda",
            slug="al-huda",
            timezone="Africa/Lagos",
            is_active=True,
        )
        teacher = self.user("legacy-teacher", Role.SUB)
        self.old("accounts", "TeacherProfile").objects.create(
            user=teacher, approved=True, max_weekly_hours=20
        )

        window = OldAvailability.objects.create(
            teacher=teacher,
            weekday=0,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
            organization=None,
        )

        self.fast_forward()

        # Availability row is preserved and now owned
        window.refresh_from_db()
        self.assertEqual(window.organization_id, org.pk)
        self.assertEqual(window.teacher_id, teacher.pk)

        # Teacher is admitted as active teacher
        membership = OldMembership.objects.get(organization_id=org.pk, user_id=teacher.pk)
        self.assertEqual(membership.role, OrganizationRole.TEACHER)
        self.assertEqual(membership.status, MembershipStatus.ACTIVE)

    def test_an_explicit_named_academy_is_used(self):
        """When multiple academies exist, SAAS_LEGACY_SCHEDULING_ORGANIZATION settles it."""
        OldOrg = self.old("organizations", "Organization")
        OldAvailability = self.old("scheduling", "Availability")

        self.old("organizations", "Organization").objects.create(
            name="Academy One", slug="academy-one", timezone="UTC", is_active=True
        )
        wanted = self.old("organizations", "Organization").objects.create(
            name="Academy Two", slug="academy-two", timezone="UTC", is_active=True
        )
        teacher = self.user("multi-teacher", Role.SUB)
        self.old("accounts", "TeacherProfile").objects.create(
            user=teacher, approved=True, max_weekly_hours=20
        )
        window = OldAvailability.objects.create(
            teacher=teacher,
            weekday=1,
            start_time_utc=time(10, 0),
            end_time_utc=time(14, 0),
            organization=None,
        )

        with mock.patch.dict(
            os.environ, {LEGACY_ORGANIZATION_ENV: "academy-two"}, clear=False
        ):
            self.fast_forward()

        window.refresh_from_db()
        self.assertEqual(window.organization_id, wanted.pk)

    def test_ownerless_availability_with_no_academy_stops_the_migration(self):
        """Unowned availability with no organization raises and stops migrate."""
        OldAvailability = self.old("scheduling", "Availability")
        teacher = self.user("stranded-teacher", Role.SUB)
        self.old("accounts", "TeacherProfile").objects.create(
            user=teacher, approved=True, max_weekly_hours=20
        )
        OldAvailability.objects.create(
            teacher=teacher,
            weekday=2,
            start_time_utc=time(8, 0),
            end_time_utc=time(12, 0),
            organization=None,
        )

        with self.assertRaises(LegacySchedulingUnmapped) as ctx:
            self.fast_forward()
        self.assertIn(LEGACY_ORGANIZATION_ENV, str(ctx.exception))

        # Operator creates the organization, and migration succeeds
        self.rewind()
        org = self.old("organizations", "Organization").objects.create(
            name="Recovered Academy", slug="recovered", timezone="UTC", is_active=True
        )
        self.fast_forward()
        self.assertEqual(
            Availability.objects.filter(organization__isnull=True).count(), 0
        )
