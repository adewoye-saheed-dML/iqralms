"""Model-layer tests for the curriculum app.

Acceptance criteria from specs/phase-2-curriculum.md that this file covers at
the model layer: 4 (a beginner skip is auto-placed and self-reviewed), 5 (audio
and skip are mutually exclusive) and 6 (re-submitting updates the existing row).
The same three are covered again through HTTP in test_api.py — the rules live in
``save()`` precisely so they hold for direct ORM writes too.

SaaS Phase 3 added the tenant invariants, and they are tested here rather than
only at the API because that is where they live: a data migration, the admin and a
direct ORM write all go through ``save()`` and none of them goes through a view.
The API-level proof that one academy cannot reach another's rows is
``test_tenant_isolation.py``.
"""

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.tests.factories import (
    LeadTeacherFactory,
    ParentFactory,
    StudentFactory,
    SubTeacherFactory,
)
from curriculum.exceptions import PlacementAlreadyReviewed, TrackHasNoFirstLevel
from curriculum.models import Level, PlacementResult, Status, TeacherTrack, Track
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import (
    OrganizationFactory,
    OrganizationMembershipFactory,
)

from .factories import (
    AUDIO_BYTES,
    BeginnerSkipPlacementFactory,
    LevelFactory,
    PlacementResultFactory,
    ReviewedPlacementFactory,
    TeacherTrackFactory,
    TrackFactory,
    TrackWithLevelsFactory,
    admit,
)


def audio_upload(name="recitation.mp3"):
    return SimpleUploadedFile(name, AUDIO_BYTES, content_type="audio/mpeg")


class TrackModelTests(TestCase):
    def test_track_can_be_created(self):
        organization = OrganizationFactory()
        track = Track.objects.create(
            organization=organization, name="Tajweed", slug="tajweed"
        )
        track.refresh_from_db()
        self.assertEqual(track.organization, organization)
        self.assertEqual(track.name, "Tajweed")
        self.assertEqual(track.slug, "tajweed")
        self.assertEqual(str(track), "Tajweed")

    def test_a_track_needs_an_owning_academy(self):
        """Tenant ownership is not optional — the whole point of the phase."""
        with self.assertRaises(ValidationError) as ctx:
            Track.objects.create(name="Ownerless", slug="ownerless")
        self.assertIn("organization", ctx.exception.message_dict)

    def test_slug_is_unique_within_one_academy(self):
        """ValidationError now, where Phase 2 documented an IntegrityError.

        Track gained a cross-row rule in SaaS Phase 3 (ownership is immutable), so
        it validates inside save() the way Level and PlacementResult always have.
        """
        track = TrackFactory(slug="hifz")
        with self.assertRaises(ValidationError) as ctx:
            Track.objects.create(
                organization=track.organization, name="Hifz Again", slug="hifz"
            )
        self.assertIn("__all__", ctx.exception.message_dict)
        self.assertEqual(Track.objects.filter(slug="hifz").count(), 1)

    def test_two_academies_may_both_teach_the_same_slug(self):
        """The rule the old global unique index made impossible."""
        here, there = OrganizationFactory(), OrganizationFactory()
        first = Track.objects.create(
            organization=here, name="Tajweed", slug="tajweed"
        )
        second = Track.objects.create(
            organization=there, name="Tajweed", slug="tajweed"
        )
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Track.objects.filter(slug="tajweed").count(), 2)

    def test_a_tracks_academy_cannot_be_changed(self):
        track = TrackFactory()
        track.organization = OrganizationFactory()
        with self.assertRaises(ValidationError) as ctx:
            track.save()
        self.assertIn("organization", ctx.exception.message_dict)

    def test_renaming_a_track_leaves_its_academy_alone(self):
        track = TrackFactory(name="Tajweed")
        owner = track.organization
        track.name = "Advanced Tajweed"
        track.save()  # must not trip the ownership-immutability check
        track.refresh_from_db()
        self.assertEqual(track.name, "Advanced Tajweed")
        self.assertEqual(track.organization, owner)

    def test_tracks_are_ordered_by_name(self):
        TrackFactory(name="Tajweed", slug="tajweed")
        TrackFactory(name="Arabic", slug="arabic")
        TrackFactory(name="Hifz", slug="hifz")
        self.assertEqual(
            list(Track.objects.values_list("name", flat=True)),
            ["Arabic", "Hifz", "Tajweed"],
        )


class LevelModelTests(TestCase):
    def test_level_can_be_created_with_spec_defaults(self):
        track = TrackFactory(name="Arabic")
        level = Level.objects.create(track=track, order=1, name="Beginner")
        level.refresh_from_db()
        self.assertEqual(level.track, track)
        self.assertEqual(level.order, 1)
        self.assertEqual(level.name, "Beginner")
        # min_age is informational and optional; cohorts are opt-in.
        self.assertIsNone(level.min_age)
        self.assertFalse(level.group_eligible)
        self.assertEqual(str(level), "Arabic 1. Beginner")

    def test_min_age_and_group_eligible_are_stored(self):
        level = LevelFactory(min_age=7, group_eligible=True)
        level.refresh_from_db()
        self.assertEqual(level.min_age, 7)
        self.assertTrue(level.group_eligible)

    def test_levels_come_back_in_track_order(self):
        track = TrackFactory()
        for name in ("Beginner", "Intermediate", "Advanced"):
            LevelFactory(track=track, name=name)
        self.assertEqual(
            list(track.levels.values_list("order", "name")),
            [(1, "Beginner"), (2, "Intermediate"), (3, "Advanced")],
        )

    def test_first_level_in_a_track_must_be_order_one(self):
        with self.assertRaises(ValidationError) as ctx:
            Level.objects.create(track=TrackFactory(), order=2, name="Second")
        self.assertIn("order", ctx.exception.message_dict)

    def test_orders_cannot_skip_a_position(self):
        """The 'no gaps' rule, enforced at save time rather than by convention."""
        track = TrackFactory()
        LevelFactory(track=track)  # order 1
        with self.assertRaises(ValidationError) as ctx:
            Level.objects.create(track=track, order=3, name="Third")
        self.assertIn("order", ctx.exception.message_dict)
        self.assertEqual(track.levels.count(), 1)

    def test_duplicate_order_in_the_same_track_is_rejected(self):
        track = TrackFactory()
        LevelFactory(track=track)  # order 1
        with self.assertRaises(ValidationError):
            Level.objects.create(track=track, order=1, name="Also first")
        self.assertEqual(track.levels.count(), 1)

    def test_the_same_order_in_a_different_track_is_fine(self):
        first, second = LevelFactory(), LevelFactory()
        self.assertEqual(first.order, second.order)
        self.assertNotEqual(first.track_id, second.track_id)

    def test_an_existing_levels_order_cannot_be_changed(self):
        level = LevelFactory()
        level.order = 4
        with self.assertRaises(ValidationError) as ctx:
            level.save()
        self.assertIn("order", ctx.exception.message_dict)

    def test_editing_a_level_leaves_its_order_alone(self):
        level = LevelFactory(name="Beginner")
        level.name = "Absolute Beginner"
        level.group_eligible = True
        level.save()  # must not trip the order-immutability check
        level.refresh_from_db()
        self.assertEqual(level.name, "Absolute Beginner")
        self.assertTrue(level.group_eligible)

    def test_next_order_for_accepts_a_track_or_its_pk(self):
        track = TrackFactory()
        self.assertEqual(Level.next_order_for(track), 1)
        self.assertEqual(Level.next_order_for(track.pk), 1)
        LevelFactory(track=track)
        self.assertEqual(Level.next_order_for(track), 2)
        self.assertEqual(Level.next_order_for(track.pk), 2)


class PlacementResultModelTests(TestCase):
    def test_pending_placement_can_be_created(self):
        placement = PlacementResultFactory()
        placement.refresh_from_db()
        self.assertTrue(placement.audio_sample)
        self.assertFalse(placement.skipped_as_beginner)
        self.assertEqual(placement.status, Status.PENDING)
        self.assertIsNone(placement.recommended_level)
        self.assertIsNone(placement.reviewed_by)
        self.assertIsNone(placement.reviewed_at)

    def test_student_must_have_role_student(self):
        for user in (ParentFactory(), SubTeacherFactory(), LeadTeacherFactory()):
            with self.subTest(role=user.role):
                with self.assertRaises(ValidationError) as ctx:
                    PlacementResult.objects.create(
                        student=user,
                        track=TrackFactory(),
                        audio_sample=audio_upload(),
                    )
                self.assertIn("student", ctx.exception.message_dict)

    def test_neither_audio_nor_skip_is_rejected(self):
        with self.assertRaises(ValidationError):
            PlacementResult.objects.create(
                student=StudentFactory(), track=TrackFactory()
            )
        self.assertFalse(PlacementResult.objects.exists())

    def test_both_audio_and_skip_is_rejected(self):
        """Acceptance criterion 5, at the model layer."""
        with self.assertRaises(ValidationError):
            PlacementResult.objects.create(
                student=StudentFactory(),
                track=TrackWithLevelsFactory(),
                audio_sample=audio_upload(),
                skipped_as_beginner=True,
            )
        self.assertFalse(PlacementResult.objects.exists())

    def test_one_placement_per_student_per_track(self):
        placement = PlacementResultFactory()
        with self.assertRaises(ValidationError):
            PlacementResult.objects.create(
                student=placement.student,
                track=placement.track,
                audio_sample=audio_upload(),
            )
        self.assertEqual(PlacementResult.objects.count(), 1)

    def test_a_student_can_be_placed_separately_in_each_track(self):
        student = StudentFactory()
        PlacementResultFactory(student=student, track=TrackFactory())
        PlacementResultFactory(student=student, track=TrackFactory())
        self.assertEqual(student.placement_results.count(), 2)

    def test_recommended_level_must_belong_to_the_placements_track(self):
        placement = PlacementResultFactory()
        foreign_level = LevelFactory()
        placement.recommended_level = foreign_level
        placement.reviewed_at = dj_timezone.now()
        with self.assertRaises(ValidationError) as ctx:
            placement.save()
        self.assertIn("recommended_level", ctx.exception.message_dict)

    def test_a_level_without_a_review_timestamp_is_rejected(self):
        """status derives from reviewed_at, so the two must not disagree."""
        track = TrackWithLevelsFactory()
        placement = PlacementResultFactory(track=track)
        placement.recommended_level = track.levels.first()
        with self.assertRaises(ValidationError):
            placement.save()

    def test_str_names_the_student_track_and_status(self):
        placement = PlacementResultFactory()
        self.assertEqual(
            str(placement),
            f"{placement.student.username} / {placement.track.name} (pending)",
        )


class BeginnerSkipTests(TestCase):
    def test_skip_is_immediately_reviewed_at_the_tracks_first_level(self):
        """Acceptance criterion 4, at the model layer."""
        placement = BeginnerSkipPlacementFactory()
        placement.refresh_from_db()

        self.assertTrue(placement.skipped_as_beginner)
        self.assertFalse(placement.audio_sample)
        self.assertEqual(placement.status, Status.REVIEWED)
        self.assertEqual(placement.recommended_level.order, 1)
        self.assertEqual(placement.recommended_level.track_id, placement.track_id)
        self.assertIsNotNone(placement.reviewed_at)
        # A system decision, not a teacher's — so nobody is stamped on it.
        self.assertIsNone(placement.reviewed_by)

    def test_skip_into_a_track_with_no_levels_is_refused(self):
        with self.assertRaises(TrackHasNoFirstLevel):
            BeginnerSkipPlacementFactory(track=TrackWithLevelsFactory(levels=0))
        self.assertFalse(PlacementResult.objects.exists())

    def test_skip_cannot_carry_a_reviewer(self):
        placement = BeginnerSkipPlacementFactory()
        placement.reviewed_by = LeadTeacherFactory()
        with self.assertRaises(ValidationError) as ctx:
            placement.save()
        self.assertIn("reviewed_by", ctx.exception.message_dict)


class ReviewTests(TestCase):
    def setUp(self):
        self.lead = LeadTeacherFactory()
        self.track = TrackWithLevelsFactory()
        self.placement = PlacementResultFactory(track=self.track)
        # SaaS Phase 3: a reviewer must be an active member of the academy that
        # owns the track. "A lead teacher somewhere" is not authority here.
        admit(self.lead, self.track.organization)

    def test_review_stamps_the_level_reviewer_and_timestamp(self):
        level = self.track.levels.get(order=2)
        before = dj_timezone.now()

        self.placement.review(recommended_level=level, reviewed_by=self.lead)
        self.placement.refresh_from_db()

        self.assertEqual(self.placement.recommended_level, level)
        self.assertEqual(self.placement.reviewed_by, self.lead)
        self.assertGreaterEqual(self.placement.reviewed_at, before)
        self.assertEqual(self.placement.status, Status.REVIEWED)

    def test_reviewing_an_already_reviewed_placement_is_refused(self):
        self.placement.review(
            recommended_level=self.track.levels.first(), reviewed_by=self.lead
        )
        with self.assertRaises(PlacementAlreadyReviewed):
            self.placement.review(
                recommended_level=self.track.levels.get(order=3),
                reviewed_by=self.lead,
            )
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.recommended_level.order, 1)

    def test_a_sub_teacher_cannot_be_the_reviewer(self):
        """Phase 2 decision: review is lead-only (see learnings.md)."""
        with self.assertRaises(ValidationError) as ctx:
            self.placement.review(
                recommended_level=self.track.levels.first(),
                reviewed_by=SubTeacherFactory(),
            )
        self.assertIn("reviewed_by", ctx.exception.message_dict)
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.status, Status.PENDING)

    def test_a_student_cannot_be_the_reviewer(self):
        with self.assertRaises(ValidationError) as ctx:
            self.placement.review(
                recommended_level=self.track.levels.first(),
                reviewed_by=StudentFactory(),
            )
        self.assertIn("reviewed_by", ctx.exception.message_dict)


class SubmitTests(TestCase):
    def setUp(self):
        self.student = StudentFactory()
        self.track = TrackWithLevelsFactory()
        # A student account is a global identity; being a student *of this
        # academy* is the membership, and submit() validates it.
        admit(self.student, self.track.organization)

    def test_submit_creates_a_pending_placement(self):
        placement = PlacementResult.submit(
            student=self.student, track=self.track, audio_sample=audio_upload()
        )
        self.assertEqual(placement.status, Status.PENDING)
        self.assertTrue(placement.audio_sample)
        self.assertEqual(PlacementResult.objects.count(), 1)

    def test_resubmitting_updates_the_same_row(self):
        """Acceptance criterion 6, at the model layer."""
        first = PlacementResult.submit(
            student=self.student, track=self.track, audio_sample=audio_upload()
        )
        second = PlacementResult.submit(
            student=self.student,
            track=self.track,
            audio_sample=audio_upload("second-try.mp3"),
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PlacementResult.objects.count(), 1)
        self.assertIn("second-try", second.audio_sample.name)

    def test_resubmitting_after_a_review_resets_it_to_pending(self):
        reviewed = ReviewedPlacementFactory(student=self.student, track=self.track)
        self.assertEqual(reviewed.status, Status.REVIEWED)

        again = PlacementResult.submit(
            student=self.student, track=self.track, audio_sample=audio_upload()
        )
        self.assertEqual(again.pk, reviewed.pk)
        self.assertEqual(again.status, Status.PENDING)
        self.assertIsNone(again.recommended_level)
        self.assertIsNone(again.reviewed_by)
        self.assertIsNone(again.reviewed_at)

    def test_resubmitting_as_a_beginner_replaces_the_audio_submission(self):
        PlacementResult.submit(
            student=self.student, track=self.track, audio_sample=audio_upload()
        )
        skipped = PlacementResult.submit(
            student=self.student, track=self.track, skipped_as_beginner=True
        )
        self.assertEqual(PlacementResult.objects.count(), 1)
        self.assertFalse(skipped.audio_sample)
        self.assertEqual(skipped.status, Status.REVIEWED)
        self.assertEqual(skipped.recommended_level.order, 1)

    def test_submitting_neither_is_refused(self):
        with self.assertRaises(ValidationError):
            PlacementResult.submit(student=self.student, track=self.track)


class AcademyOwnershipTests(TestCase):
    """The derived academy: one stored column, everything else reads through it."""

    def test_a_levels_academy_is_its_tracks(self):
        level = LevelFactory()
        self.assertEqual(level.organization, level.track.organization)

    def test_a_placements_academy_is_its_tracks(self):
        placement = PlacementResultFactory()
        self.assertEqual(placement.organization, placement.track.organization)

    def test_a_teacher_assignments_academy_is_its_memberships(self):
        track = TrackFactory()
        membership = admit(LeadTeacherFactory(), track.organization)
        assignment = TeacherTrackFactory(membership=membership, track=track)
        self.assertEqual(assignment.organization, track.organization)
        self.assertEqual(assignment.user, membership.user)


class PlacementTenancyTests(TestCase):
    """"Student is active here" and "reviewer is active here", at the model layer."""

    def test_a_student_outside_the_academy_cannot_be_placed(self):
        track = TrackFactory()
        outsider = StudentFactory()  # a student account, and a stranger here
        with self.assertRaises(ValidationError) as ctx:
            PlacementResult.objects.create(
                student=outsider, track=track, audio_sample=audio_upload()
            )
        self.assertIn("student", ctx.exception.message_dict)
        self.assertFalse(PlacementResult.objects.exists())

    def test_membership_in_another_academy_does_not_count(self):
        track = TrackFactory()
        student = StudentFactory()
        admit(student, OrganizationFactory())  # a member, but not here
        with self.assertRaises(ValidationError) as ctx:
            PlacementResult.objects.create(
                student=student, track=track, audio_sample=audio_upload()
            )
        self.assertIn("student", ctx.exception.message_dict)

    def test_a_suspended_membership_is_not_access(self):
        track = TrackFactory()
        student = StudentFactory()
        OrganizationMembershipFactory(
            organization=track.organization,
            user=student,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.SUSPENDED,
        )
        with self.assertRaises(ValidationError) as ctx:
            PlacementResult.objects.create(
                student=student, track=track, audio_sample=audio_upload()
            )
        self.assertIn("student", ctx.exception.message_dict)

    def test_a_lead_outside_the_academy_cannot_be_the_reviewer(self):
        placement = PlacementResultFactory(track=TrackWithLevelsFactory())
        stranger = LeadTeacherFactory()
        with self.assertRaises(ValidationError) as ctx:
            placement.review(
                recommended_level=placement.track.levels.first(),
                reviewed_by=stranger,
            )
        self.assertIn("reviewed_by", ctx.exception.message_dict)
        placement.refresh_from_db()
        self.assertEqual(placement.status, Status.PENDING)

    def test_a_suspended_lead_cannot_review(self):
        placement = PlacementResultFactory(track=TrackWithLevelsFactory())
        lead = LeadTeacherFactory()
        OrganizationMembershipFactory(
            organization=placement.track.organization,
            user=lead,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.SUSPENDED,
        )
        with self.assertRaises(ValidationError) as ctx:
            placement.review(
                recommended_level=placement.track.levels.first(), reviewed_by=lead
            )
        self.assertIn("reviewed_by", ctx.exception.message_dict)

    def test_a_recommended_level_from_another_academy_is_refused(self):
        """The spec's named cross-academy combination, at the model layer."""
        placement = PlacementResultFactory(track=TrackWithLevelsFactory())
        lead = LeadTeacherFactory()
        admit(lead, placement.track.organization)
        elsewhere = LevelFactory()  # its own academy, its own track
        with self.assertRaises(ValidationError) as ctx:
            placement.review(recommended_level=elsewhere, reviewed_by=lead)
        self.assertIn("recommended_level", ctx.exception.message_dict)

    def test_one_student_holds_independent_placements_in_two_academies(self):
        student = StudentFactory()
        here, there = TrackFactory(slug="tajweed"), TrackFactory(slug="tajweed")
        mine = PlacementResultFactory(student=student, track=here)
        theirs = PlacementResultFactory(student=student, track=there)

        self.assertNotEqual(mine.pk, theirs.pk)
        self.assertEqual(
            list(
                PlacementResult.objects.in_organization(here.organization).values_list(
                    "pk", flat=True
                )
            ),
            [mine.pk],
        )
        self.assertEqual(
            list(
                PlacementResult.objects.in_organization(
                    there.organization
                ).values_list("pk", flat=True)
            ),
            [theirs.pk],
        )

    def test_suspending_a_student_removes_their_placement_from_the_academy(self):
        """The queryset's second half, which the track filter alone would miss."""
        placement = PlacementResultFactory()
        organization = placement.track.organization
        self.assertTrue(
            PlacementResult.objects.in_organization(organization).exists()
        )

        membership = placement.student.organization_memberships.get(
            organization=organization
        )
        membership.status = MembershipStatus.SUSPENDED
        membership.save()

        self.assertFalse(
            PlacementResult.objects.in_organization(organization).exists()
        )


class TeacherTrackModelTests(TestCase):
    def test_a_teacher_can_be_assigned_a_track_in_their_academy(self):
        track = TrackFactory(slug="tajweed")
        membership = admit(LeadTeacherFactory(), track.organization)
        assignment = TeacherTrackFactory(membership=membership, track=track)
        assignment.refresh_from_db()
        self.assertTrue(assignment.active)
        self.assertIn("tajweed", str(assignment))

    def test_a_track_from_another_academy_is_refused(self):
        """``membership.organization == track.organization``, the model's reason to exist."""
        membership = admit(LeadTeacherFactory(), OrganizationFactory())
        with self.assertRaises(ValidationError) as ctx:
            TeacherTrackFactory(membership=membership, track=TrackFactory())
        self.assertIn("track", ctx.exception.message_dict)
        self.assertFalse(TeacherTrack.objects.exists())

    def test_only_a_teaching_account_can_be_assigned_a_track(self):
        for user in (StudentFactory(), ParentFactory()):
            with self.subTest(role=user.role):
                track = TrackFactory()
                membership = admit(user, track.organization)
                with self.assertRaises(ValidationError) as ctx:
                    TeacherTrackFactory(membership=membership, track=track)
                self.assertIn("membership", ctx.exception.message_dict)

    def test_a_sub_teacher_may_hold_tracks(self):
        """Eligibility is not the lead-only review rule — subs teach."""
        track = TrackFactory()
        membership = admit(SubTeacherFactory(), track.organization)
        self.assertTrue(TeacherTrackFactory(membership=membership, track=track).pk)

    def test_the_same_track_cannot_be_assigned_twice(self):
        track = TrackFactory()
        membership = admit(LeadTeacherFactory(), track.organization)
        TeacherTrackFactory(membership=membership, track=track)
        with self.assertRaises(ValidationError):
            TeacherTrackFactory(membership=membership, track=track)
        self.assertEqual(TeacherTrack.objects.count(), 1)

    def test_one_teacher_teaches_different_tracks_in_different_academies(self):
        """The phase's headline requirement, at the model layer."""
        teacher = SubTeacherFactory()
        tajweed = TrackFactory(slug="tajweed")
        arabic = TrackFactory(slug="arabic")
        here = TeacherTrackFactory(
            membership=admit(teacher, tajweed.organization), track=tajweed
        )
        there = TeacherTrackFactory(
            membership=admit(teacher, arabic.organization), track=arabic
        )

        self.assertEqual(teacher.organization_memberships.count(), 2)
        self.assertEqual(
            list(
                TeacherTrack.objects.in_organization(
                    tajweed.organization
                ).values_list("pk", flat=True)
            ),
            [here.pk],
        )
        self.assertEqual(
            list(
                TeacherTrack.objects.in_organization(arabic.organization).values_list(
                    "pk", flat=True
                )
            ),
            [there.pk],
        )

    def test_withdrawing_eligibility_keeps_the_row(self):
        track = TrackFactory()
        assignment = TeacherTrackFactory(
            membership=admit(LeadTeacherFactory(), track.organization), track=track
        )
        assignment.active = False
        assignment.save()

        self.assertEqual(TeacherTrack.objects.count(), 1)
        self.assertFalse(TeacherTrack.objects.active().exists())


class MigrationStateTests(TestCase):
    def test_no_model_changes_are_missing_a_migration(self):
        """Guards acceptance criterion 1 against later model edits."""
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
