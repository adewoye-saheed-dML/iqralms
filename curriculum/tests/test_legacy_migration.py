"""The pre-SaaS curriculum backfill — the decision, and the migration itself.

Two layers, because they fail in different ways.

``LegacyOrganizationChoiceTests`` covers ``curriculum.legacy``'s one judgement
call: which academy owns curriculum that predates multi-tenancy. Getting that wrong
does not raise — it silently hands one tenant another's syllabus and student
placements — so every branch is asserted, including both refusals.

``LegacyCurriculumMigrationTests`` runs the real migrations against a real database
that starts in the pre-SaaS shape: tracks with no owner, levels hanging off them,
placements whose students hold no membership, and a teacher whose specialties are
recorded only in the legacy global relation. It migrates back to the nullable state,
builds that data through historical models, and migrates forward — which is the only
way to test a data migration rather than a paraphrase of one.
"""

import os
from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone as dj_timezone

from accounts.models import Role, TeacherProfile
from accounts.tests.factories import (
    StudentFactory,
    SubTeacherFactory,
    TeacherProfileFactory,
)
from curriculum.legacy import (
    LEGACY_ORGANIZATION_ENV,
    LegacyCurriculumUnmapped,
    admit_legacy_participants,
    backfill_teacher_tracks,
    choose_legacy_organization,
)
from curriculum.models import Level, PlacementResult, Status, TeacherTrack, Track
from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
)
from organizations.tests.factories import OrganizationFactory

from .factories import TrackFactory, admit


class LegacyOrganizationChoiceTests(TestCase):
    """"Resolve or refuse" — never guess. One test per branch."""

    def test_nothing_to_own_means_nothing_to_do(self):
        """A fresh install, and every test database, takes this path."""
        OrganizationFactory()
        self.assertIsNone(
            choose_legacy_organization(
                Organization=Organization, curriculum_needs_owner=False
            )
        )

    def test_one_organization_is_unambiguous(self):
        only = OrganizationFactory()
        self.assertEqual(
            choose_legacy_organization(Organization=Organization), only
        )

    def test_no_organization_refuses_rather_than_inventing_one(self):
        with self.assertRaises(LegacyCurriculumUnmapped) as ctx:
            choose_legacy_organization(Organization=Organization)
        # The message has to tell an operator what to do, or the refusal is just
        # a stuck deployment.
        self.assertIn(LEGACY_ORGANIZATION_ENV, str(ctx.exception))

    def test_two_organizations_refuse_rather_than_picking_one(self):
        OrganizationFactory()
        OrganizationFactory()
        with self.assertRaises(LegacyCurriculumUnmapped) as ctx:
            choose_legacy_organization(Organization=Organization)
        self.assertIn(LEGACY_ORGANIZATION_ENV, str(ctx.exception))

    def test_an_explicit_primary_key_settles_it(self):
        OrganizationFactory()
        wanted = OrganizationFactory()
        self.assertEqual(
            choose_legacy_organization(
                Organization=Organization, named=str(wanted.pk)
            ),
            wanted,
        )

    def test_an_explicit_slug_settles_it_too(self):
        OrganizationFactory()
        wanted = OrganizationFactory(slug="al-huda")
        self.assertEqual(
            choose_legacy_organization(Organization=Organization, named="al-huda"),
            wanted,
        )

    def test_a_name_that_matches_nothing_is_refused(self):
        OrganizationFactory()
        with self.assertRaises(LegacyCurriculumUnmapped):
            choose_legacy_organization(Organization=Organization, named="no-such")

    def test_an_explicit_name_wins_over_a_single_candidate(self):
        """So an operator can still correct a single-organization deployment."""
        OrganizationFactory(slug="wrong-one")
        wanted = OrganizationFactory(slug="right-one")
        self.assertEqual(
            choose_legacy_organization(
                Organization=Organization, named="right-one"
            ),
            wanted,
        )


class LegacySpecialtyBackfillTests(TestCase):
    """The specialty conversion, on models that do not need a nullable column.

    ``TeacherProfile.specialties`` stays exactly where it is — scheduling still
    reads it until SaaS Phase 4 — so what is asserted here is that a *tenant-safe
    equivalent* appears beside it, for this academy only.
    """

    def setUp(self):
        self.organization = OrganizationFactory(slug="al-huda")
        self.tajweed = TrackFactory(organization=self.organization, slug="tajweed")
        self.elsewhere = TrackFactory(slug="tajweed")

    def profile(self, user=None, tracks=()):
        profile = TeacherProfileFactory(user=user or SubTeacherFactory())
        for track in tracks:
            profile.specialties.add(track)
        return profile

    def test_a_legacy_specialty_becomes_academy_scoped_eligibility(self):
        profile = self.profile(tracks=[self.tajweed])
        membership = admit(profile.user, self.organization)

        created, skipped = backfill_teacher_tracks(
            Track=Track,
            TeacherProfile=TeacherProfile,
            TeacherTrack=TeacherTrack,
            OrganizationMembership=OrganizationMembership,
            organization=self.organization,
        )
        self.assertEqual(created, 1)
        self.assertEqual(skipped, [])

        assignment = TeacherTrack.objects.get()
        self.assertEqual(assignment.membership, membership)
        self.assertEqual(assignment.track, self.tajweed)
        self.assertTrue(assignment.active)
        # The legacy relation is untouched: scheduling still reads it.
        self.assertEqual(list(profile.specialties.all()), [self.tajweed])

    def test_it_is_not_copied_into_an_unrelated_academy(self):
        profile = self.profile(tracks=[self.tajweed])
        admit(profile.user, self.organization)
        other = OrganizationFactory()

        backfill_teacher_tracks(
            Track=Track,
            TeacherProfile=TeacherProfile,
            TeacherTrack=TeacherTrack,
            OrganizationMembership=OrganizationMembership,
            organization=self.organization,
        )
        self.assertFalse(
            TeacherTrack.objects.in_organization(other).exists()
        )

    def test_a_specialty_in_another_academys_track_is_left_alone(self):
        profile = self.profile(tracks=[self.elsewhere])
        admit(profile.user, self.organization)

        created, skipped = backfill_teacher_tracks(
            Track=Track,
            TeacherProfile=TeacherProfile,
            TeacherTrack=TeacherTrack,
            OrganizationMembership=OrganizationMembership,
            organization=self.organization,
        )
        self.assertEqual((created, skipped), (0, []))
        self.assertFalse(TeacherTrack.objects.exists())

    def test_an_unmappable_specialty_is_reported_rather_than_forced(self):
        """No membership here, so there is nothing to anchor eligibility to.

        The phase spec asks for an unmappable specialty to be documented instead
        of guessed at, and a return value is how a migration says so.
        """
        profile = self.profile(tracks=[self.tajweed])

        created, skipped = backfill_teacher_tracks(
            Track=Track,
            TeacherProfile=TeacherProfile,
            TeacherTrack=TeacherTrack,
            OrganizationMembership=OrganizationMembership,
            organization=self.organization,
        )
        self.assertEqual(created, 0)
        self.assertEqual(skipped, [(profile.user.username, "tajweed")])
        self.assertFalse(TeacherTrack.objects.exists())

    def test_a_suspended_membership_does_not_anchor_eligibility_either(self):
        profile = self.profile(tracks=[self.tajweed])
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=profile.user,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.SUSPENDED,
        )
        created, skipped = backfill_teacher_tracks(
            Track=Track,
            TeacherProfile=TeacherProfile,
            TeacherTrack=TeacherTrack,
            OrganizationMembership=OrganizationMembership,
            organization=self.organization,
        )
        self.assertEqual(created, 0)
        self.assertEqual(len(skipped), 1)

    def test_running_it_twice_creates_nothing_the_second_time(self):
        profile = self.profile(tracks=[self.tajweed])
        admit(profile.user, self.organization)
        kwargs = dict(
            Track=Track,
            TeacherProfile=TeacherProfile,
            TeacherTrack=TeacherTrack,
            OrganizationMembership=OrganizationMembership,
            organization=self.organization,
        )
        self.assertEqual(backfill_teacher_tracks(**kwargs)[0], 1)
        self.assertEqual(backfill_teacher_tracks(**kwargs)[0], 0)
        self.assertEqual(TeacherTrack.objects.count(), 1)


class LegacyParticipantAdmissionTests(TestCase):
    """Who the backfill admits: the people who already hold curriculum data."""

    def setUp(self):
        self.organization = OrganizationFactory(slug="al-huda")
        self.track = TrackFactory(organization=self.organization, slug="tajweed")

    def admit_participants(self):
        return admit_legacy_participants(
            Track=Track,
            PlacementResult=PlacementResult,
            TeacherProfile=TeacherProfile,
            OrganizationMembership=OrganizationMembership,
            organization=self.organization,
        )

    def test_a_teacher_with_a_recorded_specialty_is_admitted_as_a_teacher(self):
        profile = TeacherProfileFactory(user=SubTeacherFactory())
        profile.specialties.add(self.track)

        self.assertEqual(self.admit_participants(), 1)
        membership = OrganizationMembership.objects.get(
            organization=self.organization, user=profile.user
        )
        self.assertEqual(membership.role, OrganizationRole.TEACHER)
        self.assertTrue(membership.is_active)

    def test_an_existing_membership_is_left_exactly_as_it_was(self):
        """Including a suspended one — a migration must not restore access."""
        profile = TeacherProfileFactory(user=SubTeacherFactory())
        profile.specialties.add(self.track)
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=profile.user,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.SUSPENDED,
        )

        self.assertEqual(self.admit_participants(), 0)
        membership = OrganizationMembership.objects.get(
            organization=self.organization, user=profile.user
        )
        self.assertEqual(membership.status, MembershipStatus.SUSPENDED)

    def test_nobody_else_is_admitted(self):
        """"Do not assign every teacher every track as a convenience", extended."""
        StudentFactory()
        TeacherProfileFactory(user=SubTeacherFactory())  # no specialties

        self.assertEqual(self.admit_participants(), 0)
        self.assertFalse(
            OrganizationMembership.objects.filter(
                organization=self.organization
            ).exists()
        )

    def test_a_specialty_in_another_academy_admits_nobody_here(self):
        profile = TeacherProfileFactory(user=SubTeacherFactory())
        profile.specialties.add(TrackFactory())

        self.assertEqual(self.admit_participants(), 0)


class LegacyCurriculumMigrationTests(TransactionTestCase):
    """The migrations themselves, against a database in the pre-SaaS shape.

    ``TransactionTestCase`` because migrating runs DDL and commits, which a
    ``TestCase``'s wrapping transaction cannot contain. The database is rolled back
    to the nullable state, filled with data that could only exist before this phase,
    and migrated forward again — so what is asserted is the code an operator will
    actually run rather than a paraphrase of it.

    Each test costs four schema migrations plus a full flush, so assertions are
    grouped by *scenario* rather than split one per fact. What distinguishes these
    tests from each other is the starting database, not the field being checked.

    ``tearDown`` always migrates back to head, whatever the test did, so a failure
    here cannot leave the schema behind for the rest of the suite.
    """

    #: The state where ``Track.organization`` exists and is still nullable. The
    #: accounts and organizations leaves are named too, because the historical
    #: models these tests build with must include ``TeacherProfile.specialties`` —
    #: the whole subject of the specialty backfill, and absent from the frozen state
    #: that ``curriculum.0002`` pins on its own.
    BEFORE = [
        ("curriculum", "0002_curriculum_organization_ownership"),
        ("accounts", "0003_organizationteacherconfiguration"),
        ("organizations", "0001_initial"),
    ]
    #: The state where ownership is required and slugs are academy-scoped.
    AFTER = [("curriculum", "0004_enforce_curriculum_tenancy")]

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
        executor.migrate(executor.loader.graph.leaf_nodes())
        executor.loader.build_graph()

    # --- building the pre-SaaS state ----------------------------------------

    def old(self, app_label, model_name):
        return self.old_apps.get_model(app_label, model_name)

    def user(self, username, role, code):
        return self.old("accounts", "User").objects.create(
            username=username,
            email=f"{username}@example.com",
            role=role,
            timezone="Africa/Lagos",
            signup_code=code,
            password="x",
        )

    def legacy_curriculum(self):
        """Two ownerless tracks, their levels, two placements, one specialty.

        Exactly the shape the single-academy product left behind: curriculum with
        no organization column filled in, and participants who hold no membership
        because memberships did not exist when the rows were written.
        """
        Track = self.old("curriculum", "Track")
        Level = self.old("curriculum", "Level")
        Placement = self.old("curriculum", "PlacementResult")

        tajweed = Track.objects.create(name="Tajweed", slug="tajweed")
        hifz = Track.objects.create(name="Hifz", slug="hifz")
        first = Level.objects.create(track=tajweed, order=1, name="Beginner")
        Level.objects.create(track=tajweed, order=2, name="Intermediate")
        Level.objects.create(track=hifz, order=1, name="Juz 30")

        student = self.user("legacy-student", Role.STUDENT, "LEGACY01")
        lead = self.user("legacy-lead", Role.LEAD, "LEGACY02")

        reviewed = Placement.objects.create(
            student=student,
            track=tajweed,
            skipped_as_beginner=True,
            recommended_level=first,
            reviewed_at=dj_timezone.now(),
            status="reviewed",
        )
        pending = Placement.objects.create(
            student=student,
            track=hifz,
            audio_sample="placements/1/legacy.mp3",
            skipped_as_beginner=False,
            status="pending",
        )

        # The legacy global specialty relation, which before this phase was the
        # only record of what this teacher taught.
        profile = self.old("accounts", "TeacherProfile").objects.create(
            user=lead, bio="", max_weekly_hours=20, is_lead=True, approved=True
        )
        profile.specialties.add(tajweed)

        return {
            "track_pks": [tajweed.pk, hifz.pk],
            "placement_pks": {reviewed.pk, pending.pk},
        }

    def named_academy(self, slug="al-huda"):
        """An academy created the way an operator would, before migrating."""
        organization = self.old("organizations", "Organization").objects.create(
            name=f"Academy {slug}",
            slug=slug,
            timezone="Africa/Lagos",
            is_active=True,
        )
        self.old("organizations", "OrganizationMembership").objects.create(
            organization=organization,
            user=self.user(f"founder-{slug}", Role.LEAD, slug[:8].upper()),
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        return organization

    # --- the tests -----------------------------------------------------------

    def test_an_empty_curriculum_migrates_with_nothing_to_do(self):
        """The normal deployment path, and the one every test database takes."""
        self.fast_forward()
        self.assertFalse(Track.objects.exists())
        self.assertFalse(TeacherTrack.objects.exists())

    def test_legacy_curriculum_is_assigned_and_stays_readable(self):
        """One scenario, the whole spec section: nothing dropped, nothing invented."""
        legacy = self.legacy_curriculum()
        organization = self.named_academy()

        self.fast_forward()

        # Tracks: every one owned by the single academy, and the same rows —
        # primary keys are preserved rather than recreated.
        self.assertEqual(Track.objects.count(), 2)
        self.assertEqual(
            set(Track.objects.values_list("organization_id", flat=True)),
            {organization.pk},
        )
        self.assertEqual(
            sorted(Track.objects.values_list("pk", flat=True)),
            sorted(legacy["track_pks"]),
        )

        # Levels: untouched, still in order, still attached to their track.
        self.assertEqual(Level.objects.count(), 3)
        self.assertEqual(
            list(
                Track.objects.get(slug="tajweed").levels.values_list("order", "name")
            ),
            [(1, "Beginner"), (2, "Intermediate")],
        )

        # Placements: all present, and *readable in the academy* — which only
        # holds because the backfill admitted their student, since
        # ``in_organization`` requires an active membership.
        self.assertEqual(
            set(PlacementResult.objects.values_list("pk", flat=True)),
            legacy["placement_pks"],
        )
        self.assertEqual(
            set(
                PlacementResult.objects.in_organization(organization).values_list(
                    "pk", flat=True
                )
            ),
            legacy["placement_pks"],
        )
        reviewed = PlacementResult.objects.get(track__slug="tajweed")
        self.assertEqual(reviewed.status, Status.REVIEWED)
        self.assertEqual(reviewed.recommended_level.order, 1)

        # Memberships: the participants who already held curriculum data, and
        # nobody else. The founder's owner row was there before.
        memberships = {
            membership.user.username: membership.role
            for membership in OrganizationMembership.objects.filter(
                organization_id=organization.pk
            ).select_related("user")
        }
        self.assertEqual(
            memberships,
            {
                "founder-al-huda": OrganizationRole.OWNER,
                "legacy-student": OrganizationRole.STAFF,
                "legacy-lead": OrganizationRole.TEACHER,
            },
        )

        # Specialties: one tenant-scoped equivalent for the one recorded track —
        # not every track in the academy — and the legacy relation still intact,
        # because scheduling reads it until SaaS Phase 4.
        assignment = TeacherTrack.objects.get()
        self.assertEqual(assignment.membership.user.username, "legacy-lead")
        self.assertEqual(assignment.membership.organization_id, organization.pk)
        self.assertEqual(assignment.track.slug, "tajweed")
        self.assertTrue(assignment.active)
        self.assertEqual(
            list(
                TeacherProfile.objects.get(
                    user__username="legacy-lead"
                ).specialties.values_list("slug", flat=True)
            ),
            ["tajweed"],
        )

    def test_an_explicitly_named_academy_is_used(self):
        """Two academies, so only the environment variable can settle it."""
        self.legacy_curriculum()
        self.named_academy(slug="al-huda")
        wanted = self.named_academy(slug="al-furqan")

        with mock.patch.dict(
            os.environ, {LEGACY_ORGANIZATION_ENV: "al-furqan"}, clear=False
        ):
            self.fast_forward()

        self.assertEqual(
            set(Track.objects.values_list("organization_id", flat=True)), {wanted.pk}
        )
        # Nothing landed in the academy that was not named.
        self.assertEqual(
            TeacherTrack.objects.exclude(
                membership__organization_id=wanted.pk
            ).count(),
            0,
        )

    def test_ownerless_curriculum_with_no_academy_stops_the_migration(self):
        """The spec's "stop and ask", as a real ``migrate`` failure.

        The recovery at the end is the other half of the claim: once the operator
        has created the academy the message asked for, the same migration runs to
        completion. A refusal that could not be recovered from would be a broken
        deployment rather than a safe one.
        """
        self.legacy_curriculum()

        with self.assertRaises(LegacyCurriculumUnmapped) as ctx:
            self.fast_forward()
        self.assertIn(LEGACY_ORGANIZATION_ENV, str(ctx.exception))

        self.rewind()
        self.named_academy()
        self.fast_forward()
        self.assertEqual(Track.objects.filter(organization__isnull=True).count(), 0)

    def test_the_slug_index_becomes_academy_scoped(self):
        self.legacy_curriculum()
        self.named_academy()

        self.fast_forward()

        # The same slug is now free in a second academy, which the old global
        # unique index made impossible.
        elsewhere = OrganizationFactory(slug="elsewhere")
        Track.objects.create(organization=elsewhere, name="Tajweed", slug="tajweed")
        self.assertEqual(Track.objects.filter(slug="tajweed").count(), 2)
