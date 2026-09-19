"""Model-layer tests for the assessment app.

The rules live in ``clean()``/``save()`` and the service methods, so this is where
they are asserted — the API tests prove the same behaviour over HTTP, and both
matter for the reason Phase 1 set out: a rule that only the endpoint enforces is a
rule a direct ORM write can ignore.

CLAUDE.md's assessment invariants are the spine of this file, one class per
concern, plus the two things it is emphatic about: historical criterion names stay
readable after live rubric edits, and missing assessments are never zeroes.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.tests.factories import (
    LeadTeacherFactory,
    ParentFactory,
    StudentFactory,
    SubTeacherFactory,
)
from assessment.models import (
    AssessmentCriterion,
    AssessmentRubric,
    AssessmentScore,
    ProgressSnapshot,
    SessionAssessment,
    average_of,
    criterion_averages_for,
)
from curriculum.models import PlacementResult
from curriculum.tests.factories import (
    LevelFactory,
    ReviewedPlacementFactory,
    TrackFactory,
    admit,
)
from organizations.tests.factories import OrganizationFactory
from scheduling.models import BookingStatus
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableTeacherFactory,
    BookingFactory,
    CancelledBookingFactory,
    ensure_teacher_configured,
    teaches,
)

from .factories import (
    DEFAULT_SCORE,
    AssessmentCriterionFactory,
    AssessmentRubricFactory,
    FlaggedAssessmentFactory,
    ProgressSnapshotFactory,
    ReviewedAssessmentFactory,
    RubricWithCriteriaFactory,
    SessionAssessmentFactory,
    past_completed_booking,
)


def scores_for(rubric, value=DEFAULT_SCORE):
    """A complete, valid payload for ``rubric``'s active criteria."""
    return [
        {"criterion": criterion, "score": value}
        for criterion in rubric.active_criteria()
    ]


class AssessmentRubricTests(TestCase):
    """CLAUDE.md's "test creation, test its key invariants"."""

    def test_a_rubric_is_created_active(self):
        rubric = AssessmentRubricFactory()

        self.assertTrue(rubric.pk)
        self.assertTrue(rubric.active)
        self.assertEqual(AssessmentRubric.active_for_track(rubric.track), rubric)

    def test_a_track_has_at_most_one_active_rubric(self):
        """The stated rule, and the reason ``track`` is not a OneToOne."""
        track = TrackFactory()
        first = AssessmentRubricFactory(track=track)
        second = AssessmentRubricFactory(track=track)

        first.refresh_from_db()
        self.assertFalse(first.active)
        self.assertTrue(second.active)
        self.assertEqual(AssessmentRubric.active_for_track(track), second)

    def test_a_superseded_rubric_is_kept_not_deleted(self):
        """Assessments already submitted point at its criteria."""
        track = TrackFactory()
        first = RubricWithCriteriaFactory(track=track)
        AssessmentRubricFactory(track=track)

        self.assertTrue(AssessmentRubric.objects.filter(pk=first.pk).exists())
        self.assertEqual(first.criteria.count(), 3)

    def test_the_database_refuses_two_active_rubrics_for_one_track(self):
        """The backstop behind ``supersede_active``, tested on its own.

        ``bulk_create`` deliberately, and only here: it is the one way to reach
        the table without ``save()`` superseding the previous rubric first, which
        is exactly the hand-written INSERT the constraint exists to catch.
        """
        track = TrackFactory()
        AssessmentRubricFactory(track=track)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssessmentRubric.objects.bulk_create(
                    [AssessmentRubric(track=track, name="Snuck in", active=True)]
                )

    def test_two_tracks_each_have_their_own_active_rubric(self):
        one, other = AssessmentRubricFactory(), AssessmentRubricFactory()

        self.assertTrue(one.active)
        self.assertTrue(other.active)
        self.assertNotEqual(one.track_id, other.track_id)

    def test_active_criteria_excludes_the_retired_ones(self):
        rubric = RubricWithCriteriaFactory()
        retired = rubric.criteria.order_by("order").last()
        retired.active = False
        retired.save()

        self.assertEqual(rubric.criteria.count(), 3)
        self.assertEqual(rubric.active_criteria().count(), 2)
        self.assertNotIn(retired, rubric.active_criteria())

    def test_a_track_with_no_rubric_has_no_active_one(self):
        self.assertIsNone(AssessmentRubric.active_for_track(TrackFactory()))


class AssessmentCriterionTests(TestCase):
    def test_a_criterion_is_created_active_and_ordered(self):
        criterion = AssessmentCriterionFactory()

        self.assertTrue(criterion.pk)
        self.assertTrue(criterion.active)
        self.assertEqual(criterion.order, 1)

    def test_criterion_order_is_unique_within_a_rubric(self):
        rubric = AssessmentRubricFactory()
        AssessmentCriterionFactory(rubric=rubric, order=1)

        with self.assertRaises(ValidationError) as ctx:
            AssessmentCriterionFactory(rubric=rubric, order=1)
        self.assertIn("__all__", ctx.exception.message_dict)

    def test_two_rubrics_may_each_have_a_criterion_at_the_same_order(self):
        one = AssessmentCriterionFactory(order=1)
        other = AssessmentCriterionFactory(order=1)

        self.assertEqual(one.order, other.order)
        self.assertNotEqual(one.rubric_id, other.rubric_id)

    def test_order_starts_at_one(self):
        with self.assertRaises(ValidationError) as ctx:
            AssessmentCriterionFactory(order=0)
        self.assertIn("order", ctx.exception.message_dict)

    def test_a_snapshot_records_identity_name_and_order(self):
        criterion = AssessmentCriterionFactory(name="Makhraj accuracy", order=1)

        self.assertEqual(
            criterion.snapshot(),
            {"id": criterion.pk, "name": "Makhraj accuracy", "order": 1},
        )


class SessionAssessmentSubmissionTests(TestCase):
    """Who may assess what, and what a complete assessment is."""

    def setUp(self):
        self.level = LevelFactory()
        self.window = AvailabilityFactory(teacher=BookableTeacherFactory())
        self.rubric = RubricWithCriteriaFactory(track=self.level.track)
        self.booking = past_completed_booking(
            availability=self.window, level=self.level
        )
        self.teacher = self.booking.teacher

    def submit(self, **overrides):
        payload = {
            "booking": self.booking,
            "assessed_by": self.teacher,
            "scores": scores_for(self.rubric),
        }
        payload.update(overrides)
        return SessionAssessment.submit(**payload)

    def test_the_assigned_teacher_assesses_a_completed_booking(self):
        assessment = self.submit(teacher_summary="Strong on makhraj.")

        self.assertTrue(assessment.pk)
        self.assertEqual(assessment.assessed_by, self.teacher)
        self.assertEqual(assessment.student, self.booking.student)
        self.assertEqual(assessment.track, self.level.track)
        self.assertEqual(assessment.teacher_summary, "Strong on makhraj.")
        self.assertEqual(assessment.scores.count(), 3)

    def test_student_and_track_are_derived_not_supplied(self):
        """They always agree with the booking — the spec's data-integrity rule."""
        assessment = self.submit()

        self.assertEqual(assessment.student_id, self.booking.student_id)
        self.assertEqual(assessment.track_id, self.booking.level.track_id)

    def test_only_one_assessment_per_booking(self):
        self.submit()

        with self.assertRaises(ValidationError) as ctx:
            self.submit()
        self.assertIn("booking", ctx.exception.message_dict)
        self.assertEqual(SessionAssessment.objects.count(), 1)

    def test_another_teacher_cannot_assess_this_booking(self):
        other = BookableTeacherFactory()
        teaches(other, self.level)

        with self.assertRaises(ValidationError) as ctx:
            self.submit(assessed_by=other)
        self.assertIn("assessed_by", ctx.exception.message_dict)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_student_cannot_assess(self):
        with self.assertRaises(ValidationError):
            self.submit(assessed_by=self.booking.student)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_parent_cannot_assess(self):
        with self.assertRaises(ValidationError):
            self.submit(assessed_by=ParentFactory())
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_scheduled_booking_cannot_be_assessed(self):
        scheduled = BookingFactory(availability=self.window, level=self.level)

        with self.assertRaises(ValidationError) as ctx:
            self.submit(booking=scheduled, assessed_by=scheduled.teacher)
        self.assertIn("booking", ctx.exception.message_dict)

    def test_a_cancelled_booking_cannot_be_assessed(self):
        cancelled = CancelledBookingFactory(availability=self.window, level=self.level)

        with self.assertRaises(ValidationError) as ctx:
            self.submit(booking=cancelled, assessed_by=cancelled.teacher)
        self.assertIn("booking", ctx.exception.message_dict)

    def test_a_no_show_booking_cannot_be_assessed(self):
        no_show = BookingFactory(availability=self.window, level=self.level)
        no_show.status = BookingStatus.NO_SHOW
        no_show.save()

        with self.assertRaises(ValidationError) as ctx:
            self.submit(booking=no_show, assessed_by=no_show.teacher)
        self.assertIn("booking", ctx.exception.message_dict)

    def test_assessment_does_not_mark_the_booking_completed(self):
        """The spec is explicit: that stays a separate act."""
        scheduled = BookingFactory(availability=self.window, level=self.level)
        with self.assertRaises(ValidationError):
            self.submit(booking=scheduled, assessed_by=scheduled.teacher)

        scheduled.refresh_from_db()
        self.assertEqual(scheduled.status, BookingStatus.SCHEDULED)

    def test_assessment_does_not_change_the_booking_at_all(self):
        before = (self.booking.status, self.booking.video_provider_meeting_id)
        self.submit()

        self.booking.refresh_from_db()
        self.assertEqual((self.booking.status, self.booking.video_provider_meeting_id), before)

    def test_a_teacher_whose_approval_was_withdrawn_cannot_assess(self):
        """The gate booking already uses, reused rather than restated.

        The approval is withdrawn *after* the session, because that is the only
        order the world allows: an unapproved teacher cannot hold availability, so
        they could never have had the booking in the first place.
        """
        profile = self.teacher.teacher_profile
        profile.approved = False
        profile.save()
        from accounts.models import OrganizationTeacherConfiguration

        OrganizationTeacherConfiguration.objects.filter(
            membership__user=self.teacher,
            membership__organization=self.booking.organization,
        ).update(approved=False)

        with self.assertRaises(ValidationError) as ctx:
            self.submit()
        self.assertIn("assessed_by", ctx.exception.message_dict)

    def test_a_track_with_no_active_rubric_cannot_be_assessed(self):
        self.rubric.active = False
        self.rubric.save()

        with self.assertRaises(ValidationError) as ctx:
            self.submit()
        self.assertIn("__all__", ctx.exception.message_dict)

    def test_a_rubric_with_no_active_criteria_cannot_be_assessed(self):
        self.rubric.criteria.update(active=False)

        with self.assertRaises(ValidationError) as ctx:
            self.submit(scores=[])
        self.assertIn("__all__", ctx.exception.message_dict)


class AssessmentCriterionSetTests(TestCase):
    """Every active criterion, exactly once, in range. Nothing implied."""

    def setUp(self):
        self.level = LevelFactory()
        self.window = AvailabilityFactory(teacher=BookableTeacherFactory())
        self.rubric = RubricWithCriteriaFactory(track=self.level.track)
        self.criteria = list(self.rubric.active_criteria())
        self.booking = past_completed_booking(
            availability=self.window, level=self.level
        )

    def submit(self, scores):
        return SessionAssessment.submit(
            booking=self.booking,
            assessed_by=self.booking.teacher,
            scores=scores,
        )

    def test_a_missing_criterion_is_rejected_not_treated_as_zero(self):
        with self.assertRaises(ValidationError) as ctx:
            self.submit(
                [{"criterion": c, "score": 4} for c in self.criteria[:-1]]
            )
        self.assertIn("scores", ctx.exception.message_dict)
        self.assertIn(self.criteria[-1].name, str(ctx.exception.message_dict["scores"]))
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_duplicated_criterion_is_rejected(self):
        payload = [{"criterion": c, "score": 4} for c in self.criteria]
        payload.append({"criterion": self.criteria[0], "score": 2})

        with self.assertRaises(ValidationError) as ctx:
            self.submit(payload)
        self.assertIn("scores", ctx.exception.message_dict)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_an_unknown_criterion_is_rejected(self):
        stranger = AssessmentCriterionFactory()
        payload = [{"criterion": c, "score": 4} for c in self.criteria]
        payload.append({"criterion": stranger, "score": 4})

        with self.assertRaises(ValidationError) as ctx:
            self.submit(payload)
        self.assertIn("scores", ctx.exception.message_dict)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_retired_criterion_is_not_an_active_one(self):
        retired = self.criteria[-1]
        retired.active = False
        retired.save()

        with self.assertRaises(ValidationError):
            self.submit([{"criterion": c, "score": 4} for c in self.criteria])

        assessment = self.submit(
            [{"criterion": c, "score": 4} for c in self.criteria[:-1]]
        )
        self.assertEqual(assessment.scores.count(), 2)

    def test_a_score_above_the_scale_is_rejected(self):
        payload = [{"criterion": c, "score": 4} for c in self.criteria]
        payload[0]["score"] = 6

        with self.assertRaises(ValidationError) as ctx:
            self.submit(payload)
        self.assertIn("score", ctx.exception.message_dict)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_score_below_the_scale_is_rejected(self):
        payload = [{"criterion": c, "score": 4} for c in self.criteria]
        payload[0]["score"] = 0

        with self.assertRaises(ValidationError):
            self.submit(payload)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_rejected_submission_leaves_nothing_behind(self):
        """The whole submission is one transaction — no half-scored assessment."""
        payload = [{"criterion": c, "score": 4} for c in self.criteria]
        payload[-1]["score"] = 9

        with self.assertRaises(ValidationError):
            self.submit(payload)
        self.assertFalse(SessionAssessment.objects.exists())
        self.assertFalse(AssessmentScore.objects.exists())

    def test_one_score_per_criterion_is_a_database_rule_too(self):
        """``bulk_create`` on purpose: it is what skips validation.

        CLAUDE.md rules out ``bulk_create`` for anything that bypasses these
        invariants, which is precisely why it is the right tool for proving the
        database still refuses when validation has been skipped.
        """
        assessment = self.submit(
            [{"criterion": c, "score": 4} for c in self.criteria]
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssessmentScore.objects.bulk_create(
                    [
                        AssessmentScore(
                            assessment=assessment,
                            criterion=self.criteria[0],
                            criterion_name=self.criteria[0].name,
                            score=3,
                        )
                    ]
                )

    def test_the_scale_is_a_database_rule_too(self):
        assessment = self.submit(
            [{"criterion": c, "score": 4} for c in self.criteria]
        )
        assessment.scores.all().delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssessmentScore.objects.bulk_create(
                    [
                        AssessmentScore(
                            assessment=assessment,
                            criterion=self.criteria[0],
                            criterion_name=self.criteria[0].name,
                            score=7,
                        )
                    ]
                )


class HistoricalRubricBehaviourTests(TestCase):
    """CLAUDE.md's historical-data rule: rubric edits affect the future only."""

    def setUp(self):
        self.level = LevelFactory()
        self.window = AvailabilityFactory(teacher=BookableTeacherFactory())
        self.rubric = RubricWithCriteriaFactory(track=self.level.track)
        self.criteria = list(self.rubric.active_criteria())
        self.assessment = SessionAssessmentFactory(
            booking=past_completed_booking(
                availability=self.window, level=self.level
            )
        )

    def second_assessment(self):
        return SessionAssessmentFactory(
            booking=past_completed_booking(
                availability=self.window, level=self.level, weeks_ago=2
            )
        )

    def test_the_assessment_snapshots_the_rubric_name(self):
        self.assertEqual(self.assessment.rubric_name, self.rubric.name)

    def test_the_assessment_snapshots_criterion_identity_name_and_order(self):
        self.assertEqual(
            self.assessment.criteria_snapshot,
            [c.snapshot() for c in self.criteria],
        )

    def test_renaming_a_criterion_leaves_existing_scores_reading_as_submitted(self):
        original = self.criteria[0].name
        self.criteria[0].name = "Makhraj — renamed by the lead"
        self.criteria[0].save()

        score = self.assessment.scores.get(criterion=self.criteria[0])
        self.assertEqual(score.criterion_name, original)

    def test_renaming_a_criterion_leaves_the_criteria_snapshot_alone(self):
        original = self.criteria[0].name
        self.criteria[0].name = "Something else entirely"
        self.criteria[0].save()

        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.criteria_snapshot[0]["name"], original)

    def test_renaming_a_criterion_changes_what_a_later_assessment_records(self):
        self.criteria[0].name = "Makhraj (2026 wording)"
        self.criteria[0].save()

        later = self.second_assessment()
        self.assertEqual(
            later.scores.get(criterion=self.criteria[0]).criterion_name,
            "Makhraj (2026 wording)",
        )
        self.assertNotEqual(
            self.assessment.scores.get(criterion=self.criteria[0]).criterion_name,
            later.scores.get(criterion=self.criteria[0]).criterion_name,
        )

    def test_retiring_a_criterion_leaves_the_old_score_in_place(self):
        retired = self.criteria[-1]
        retired.active = False
        retired.save()

        self.assertTrue(
            self.assessment.scores.filter(criterion=retired).exists()
        )
        self.assertEqual(self.assessment.scores.count(), 3)

    def test_a_later_assessment_does_not_score_a_retired_criterion(self):
        retired = self.criteria[-1]
        retired.active = False
        retired.save()

        later = self.second_assessment()
        self.assertEqual(later.scores.count(), 2)
        self.assertFalse(later.scores.filter(criterion=retired).exists())
        self.assertEqual(len(later.criteria_snapshot), 2)

    def test_superseding_the_rubric_leaves_the_old_assessment_readable(self):
        replacement = RubricWithCriteriaFactory(
            track=self.level.track, name="2027 sheet"
        )

        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.rubric_name, self.rubric.name)
        self.assertEqual(self.assessment.scores.count(), 3)
        self.assertNotEqual(self.assessment.rubric_name, replacement.name)

    def test_a_criterion_that_has_been_scored_cannot_be_deleted(self):
        """PROTECT: an assessed criterion is part of the record."""
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.criteria[0].delete()

    def test_a_new_assessment_after_superseding_uses_the_new_sheet(self):
        replacement = RubricWithCriteriaFactory(
            track=self.level.track, name="2027 sheet", criteria=2
        )

        later = self.second_assessment()
        self.assertEqual(later.rubric_name, "2027 sheet")
        self.assertEqual(later.scores.count(), 2)
        self.assertEqual(
            set(later.scores.values_list("criterion_id", flat=True)),
            set(replacement.active_criteria().values_list("pk", flat=True)),
        )


class TeacherDataImmutabilityTests(TestCase):
    """CLAUDE.md: teacher scores are immutable after submission in this phase."""

    def setUp(self):
        self.assessment = SessionAssessmentFactory(
            teacher_summary="As submitted."
        )

    def test_the_teacher_summary_cannot_be_edited(self):
        self.assessment.teacher_summary = "Rewritten later"

        with self.assertRaises(ValidationError) as ctx:
            self.assessment.save()
        self.assertIn("teacher_summary", ctx.exception.message_dict)

    def test_the_flag_cannot_be_flipped_after_submission(self):
        self.assessment.flagged_for_review = True

        with self.assertRaises(ValidationError) as ctx:
            self.assessment.save()
        self.assertIn("flagged_for_review", ctx.exception.message_dict)

    def test_the_criteria_snapshot_cannot_be_rewritten(self):
        self.assessment.criteria_snapshot = [{"id": 1, "name": "Invented", "order": 1}]

        with self.assertRaises(ValidationError) as ctx:
            self.assessment.save()
        self.assertIn("criteria_snapshot", ctx.exception.message_dict)

    def test_the_booking_cannot_be_moved_to_another_session(self):
        other = past_completed_booking()
        self.assessment.booking = other

        with self.assertRaises(ValidationError):
            self.assessment.save()

    def test_a_submitted_score_cannot_be_changed(self):
        score = self.assessment.scores.first()
        score.score = 1

        with self.assertRaises(ValidationError) as ctx:
            score.save()
        self.assertIn("__all__", ctx.exception.message_dict)

    def test_a_submitted_comment_cannot_be_changed_either(self):
        """No correction path in this phase — the spec says to ask, not invent."""
        score = self.assessment.scores.first()
        score.comment = "Meant to say something else"

        with self.assertRaises(ValidationError):
            score.save()

    def test_the_stored_score_survives_a_refused_edit(self):
        score = self.assessment.scores.first()
        original = score.score
        score.score = 1
        with self.assertRaises(ValidationError):
            score.save()

        score.refresh_from_db()
        self.assertEqual(score.score, original)


class LeadReviewTests(TestCase):
    """Review is an annotation beside the teacher's work, never an edit of it."""

    def setUp(self):
        self.lead = LeadTeacherFactory()
        self.assessment = FlaggedAssessmentFactory()
        admit(self.lead, self.assessment.organization)

    def test_a_flagged_assessment_starts_in_the_pending_queue(self):
        self.assertIn(
            self.assessment, SessionAssessment.pending_lead_review()
        )
        self.assertFalse(self.assessment.is_lead_reviewed)

    def test_an_unflagged_assessment_is_never_in_the_queue(self):
        unflagged = SessionAssessmentFactory()
        self.assertNotIn(unflagged, SessionAssessment.pending_lead_review())

    def test_reviewing_stamps_who_and_when_plus_the_note(self):
        self.assessment.record_lead_review(
            reviewed_by=self.lead, note="Spoke to the teacher; plan agreed."
        )

        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.lead_reviewed_by, self.lead)
        self.assertIsNotNone(self.assessment.lead_reviewed_at)
        self.assertEqual(
            self.assessment.lead_review_note, "Spoke to the teacher; plan agreed."
        )

    def test_reviewing_leaves_the_teachers_scores_and_summary_untouched(self):
        before = {
            "summary": self.assessment.teacher_summary,
            "scores": list(self.assessment.scores.values_list("criterion_id", "score")),
            "average": self.assessment.overall_average,
        }
        self.assessment.record_lead_review(reviewed_by=self.lead, note="Noted.")

        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.teacher_summary, before["summary"])
        self.assertEqual(
            list(self.assessment.scores.values_list("criterion_id", "score")),
            before["scores"],
        )
        self.assertEqual(self.assessment.overall_average, before["average"])

    def test_reviewing_removes_it_from_the_pending_queue(self):
        self.assessment.record_lead_review(reviewed_by=self.lead)
        self.assertNotIn(self.assessment, SessionAssessment.pending_lead_review())

    def test_reviewing_does_not_clear_the_flag(self):
        """That the teacher raised one is part of the record."""
        self.assessment.record_lead_review(reviewed_by=self.lead)

        self.assessment.refresh_from_db()
        self.assertTrue(self.assessment.flagged_for_review)
        self.assertTrue(self.assessment.flag_reason)

    def test_only_a_lead_may_be_the_reviewer(self):
        self.assessment.lead_reviewed_by = SubTeacherFactory()
        self.assessment.lead_reviewed_at = dj_timezone.now()

        with self.assertRaises(ValidationError) as ctx:
            self.assessment.save()
        self.assertIn("lead_reviewed_by", ctx.exception.message_dict)

    def test_a_review_stamp_without_a_reviewer_is_incoherent(self):
        self.assessment.lead_reviewed_at = dj_timezone.now()

        with self.assertRaises(ValidationError) as ctx:
            self.assessment.save()
        self.assertIn("__all__", ctx.exception.message_dict)

    def test_a_note_without_a_review_is_incoherent(self):
        self.assessment.lead_review_note = "Thinking out loud."

        with self.assertRaises(ValidationError) as ctx:
            self.assessment.save()
        self.assertIn("lead_review_note", ctx.exception.message_dict)

    def test_an_unflagged_assessment_may_still_be_reviewed(self):
        """The spec lets the lead monitor; only a flag *requires* review state."""
        unflagged = SessionAssessmentFactory()
        admit(self.lead, unflagged.organization)
        unflagged.record_lead_review(reviewed_by=self.lead, note="Spot check.")

        unflagged.refresh_from_db()
        self.assertTrue(unflagged.is_lead_reviewed)
        self.assertFalse(unflagged.flagged_for_review)

    def test_a_flag_reason_without_a_flag_is_refused_at_submission(self):
        booking = past_completed_booking()
        rubric = AssessmentRubric.active_for_track(booking.level.track)
        if rubric is None:
            rubric = RubricWithCriteriaFactory(track=booking.level.track)

        with self.assertRaises(ValidationError) as ctx:
            SessionAssessment.submit(
                booking=booking,
                assessed_by=booking.teacher,
                scores=scores_for(rubric),
                flagged_for_review=False,
                flag_reason="Worried, but not flagging.",
            )
        self.assertIn("flag_reason", ctx.exception.message_dict)

    def test_a_reviewed_assessment_from_the_factory_has_left_the_queue(self):
        reviewed = ReviewedAssessmentFactory()
        self.assertTrue(reviewed.is_lead_reviewed)
        self.assertNotIn(reviewed, SessionAssessment.pending_lead_review())


class AverageDefinitionTests(TestCase):
    """One score definition everywhere (CLAUDE.md), and no zero for absent data."""

    def test_the_average_is_the_mean_of_every_criterion_score(self):
        self.assertEqual(average_of([1, 2, 3, 4, 5]), Decimal("3.00"))

    def test_the_average_is_quantised_to_two_places(self):
        self.assertEqual(average_of([4, 5]), Decimal("4.50"))
        self.assertEqual(average_of([1, 2, 2]), Decimal("1.67"))

    def test_individual_scores_are_not_rounded_before_aggregating(self):
        """3 and 4 average to 3.50, not to 3 or 4."""
        self.assertEqual(average_of([3, 4]), Decimal("3.50"))

    def test_no_scores_is_none_never_zero(self):
        self.assertIsNone(average_of([]))

    def test_an_assessment_reports_its_own_average(self):
        assessment = SessionAssessmentFactory(score_value=5)
        self.assertEqual(assessment.overall_average, Decimal("5.00"))

    def test_mixed_scores_average_correctly(self):
        booking = past_completed_booking()
        rubric = RubricWithCriteriaFactory(track=booking.level.track)
        criteria = list(rubric.active_criteria())
        assessment = SessionAssessment.submit(
            booking=booking,
            assessed_by=booking.teacher,
            scores=[
                {"criterion": criteria[0], "score": 2},
                {"criterion": criteria[1], "score": 3},
                {"criterion": criteria[2], "score": 5},
            ],
        )
        self.assertEqual(assessment.overall_average, Decimal("3.33"))

    def test_a_per_criterion_average_uses_only_that_criterions_scores(self):
        window = AvailabilityFactory(teacher=BookableTeacherFactory())
        level = LevelFactory()
        rubric = RubricWithCriteriaFactory(track=level.track, criteria=2)
        first, second = rubric.active_criteria()

        for marks in ((1, 5), (3, 5)):
            SessionAssessment.submit(
                booking=past_completed_booking(availability=window, level=level),
                assessed_by=window.teacher,
                scores=[
                    {"criterion": first, "score": marks[0]},
                    {"criterion": second, "score": marks[1]},
                ],
            )

        rows = criterion_averages_for(AssessmentScore.objects.all())
        by_id = {row["criterion_id"]: row for row in rows}
        self.assertEqual(by_id[first.pk]["average"], Decimal("2.00"))
        self.assertEqual(by_id[second.pk]["average"], Decimal("5.00"))
        self.assertEqual(by_id[first.pk]["score_count"], 2)

    def test_a_per_criterion_row_uses_the_most_recent_snapshot_name(self):
        window = AvailabilityFactory(teacher=BookableTeacherFactory())
        level = LevelFactory()
        rubric = RubricWithCriteriaFactory(track=level.track, criteria=1)
        criterion = rubric.active_criteria().first()

        SessionAssessment.submit(
            booking=past_completed_booking(
                availability=window, level=level, weeks_ago=4
            ),
            assessed_by=window.teacher,
            scores=[{"criterion": criterion, "score": 3}],
        )
        criterion.name = "Makhraj (revised wording)"
        criterion.save()
        SessionAssessment.submit(
            booking=past_completed_booking(
                availability=window, level=level, weeks_ago=1
            ),
            assessed_by=window.teacher,
            scores=[{"criterion": criterion, "score": 5}],
        )

        rows = criterion_averages_for(AssessmentScore.objects.all())
        self.assertEqual(len(rows), 1, "a rename must not split one criterion in two")
        self.assertEqual(rows[0]["criterion_name"], "Makhraj (revised wording)")
        self.assertEqual(rows[0]["average"], Decimal("4.00"))

    def test_per_criterion_rows_come_back_in_sheet_order(self):
        booking = past_completed_booking()
        rubric = RubricWithCriteriaFactory(track=booking.level.track)
        criteria = list(rubric.active_criteria())
        SessionAssessment.submit(
            booking=booking,
            assessed_by=booking.teacher,
            scores=[{"criterion": c, "score": 4} for c in criteria],
        )

        rows = criterion_averages_for(AssessmentScore.objects.all())
        self.assertEqual(
            [row["criterion_id"] for row in rows], [c.pk for c in criteria]
        )


class ProgressSnapshotTests(TestCase):
    """A snapshot is a measurement that stops moving. Everything here is that."""

    def setUp(self):
        self.level = LevelFactory()
        self.track = self.level.track
        self.lead = LeadTeacherFactory()
        self.student = StudentFactory()
        admit(self.lead, self.track.organization)
        admit(self.student, self.track.organization)
        self.window = AvailabilityFactory(
            teacher=BookableTeacherFactory(),
            organization=self.track.organization,
        )
        self.rubric = RubricWithCriteriaFactory(track=self.track)
        self.period_start = dj_timezone.now() - timedelta(weeks=4)
        self.period_end = dj_timezone.now()

    def generate(self, **overrides):
        payload = {
            "student": self.student,
            "track": self.track,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "generated_by": self.lead,
        }
        payload.update(overrides)
        return ProgressSnapshot.generate(**payload)

    def taught(self, *, weeks_ago=1, assessed=True, score=DEFAULT_SCORE):
        booking = past_completed_booking(
            availability=self.window,
            level=self.level,
            student=self.student,
            weeks_ago=weeks_ago,
        )
        if assessed:
            SessionAssessmentFactory(booking=booking, score_value=score)
        return booking

    def test_a_snapshot_counts_completed_and_assessed_sessions(self):
        self.taught(weeks_ago=1)
        self.taught(weeks_ago=2, assessed=False)

        snapshot, created = self.generate()
        self.assertTrue(created)
        self.assertEqual(snapshot.completed_sessions, 2)
        self.assertEqual(snapshot.assessed_sessions, 1)

    def test_an_unassessed_session_is_not_a_zero(self):
        """CLAUDE.md: missing assessments are missing data."""
        self.taught(weeks_ago=1, score=4)
        self.taught(weeks_ago=2, assessed=False)

        snapshot, _ = self.generate()
        self.assertEqual(snapshot.overall_average, Decimal("4.00"))

    def test_a_period_with_nothing_assessed_has_no_average_at_all(self):
        self.taught(weeks_ago=1, assessed=False)

        snapshot, _ = self.generate()
        self.assertEqual(snapshot.completed_sessions, 1)
        self.assertEqual(snapshot.assessed_sessions, 0)
        self.assertIsNone(snapshot.overall_average)

    def test_sessions_outside_the_period_are_not_counted(self):
        self.taught(weeks_ago=1)
        self.taught(weeks_ago=10)

        snapshot, _ = self.generate()
        self.assertEqual(snapshot.completed_sessions, 1)
        self.assertEqual(snapshot.assessed_sessions, 1)

    def test_another_students_sessions_are_not_counted(self):
        self.taught(weeks_ago=1)
        other = past_completed_booking(availability=self.window, level=self.level)
        SessionAssessmentFactory(booking=other)

        snapshot, _ = self.generate()
        self.assertEqual(snapshot.completed_sessions, 1)

    def test_another_tracks_sessions_are_not_counted(self):
        self.taught(weeks_ago=1)
        elsewhere = LevelFactory()
        RubricWithCriteriaFactory(track=elsewhere.track)
        SessionAssessmentFactory(
            booking=past_completed_booking(
                availability=self.window, level=elsewhere, student=self.student
            )
        )

        snapshot, _ = self.generate()
        self.assertEqual(snapshot.completed_sessions, 1)
        self.assertEqual(snapshot.assessed_sessions, 1)

    def test_the_criterion_averages_are_snapshotted_as_json(self):
        self.taught(weeks_ago=1, score=5)

        snapshot, _ = self.generate()
        self.assertEqual(len(snapshot.criterion_averages), 3)
        row = snapshot.criterion_averages[0]
        self.assertEqual(set(row), {"criterion_id", "criterion_name", "average", "score_count"})
        # A string, not a float: JSON has no decimal, and "5.00" stays exact.
        self.assertEqual(row["average"], "5.00")

    def test_repeating_the_request_does_not_create_a_duplicate(self):
        self.taught(weeks_ago=1)
        first, created_first = self.generate()
        second, created_second = self.generate()

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ProgressSnapshot.objects.count(), 1)

    def test_a_later_assessment_does_not_move_a_generated_snapshot(self):
        """The whole point of a snapshot."""
        self.taught(weeks_ago=1, score=5)
        snapshot, _ = self.generate()
        before = (snapshot.assessed_sessions, snapshot.overall_average)

        self.taught(weeks_ago=2, score=1)

        snapshot.refresh_from_db()
        self.assertEqual((snapshot.assessed_sessions, snapshot.overall_average), before)

    def test_a_different_period_is_a_different_snapshot(self):
        self.taught(weeks_ago=1)
        self.generate()
        other, created = self.generate(
            period_start=self.period_start - timedelta(weeks=8),
            period_end=self.period_start,
        )

        self.assertTrue(created)
        self.assertEqual(ProgressSnapshot.objects.count(), 2)

    def test_a_generated_snapshot_is_immutable(self):
        self.taught(weeks_ago=1)
        snapshot, _ = self.generate()
        snapshot.assessed_sessions = 99

        with self.assertRaises(ValidationError) as ctx:
            snapshot.save()
        self.assertIn("assessed_sessions", ctx.exception.message_dict)

    def test_the_average_on_a_snapshot_cannot_be_edited(self):
        self.taught(weeks_ago=1)
        snapshot, _ = self.generate()
        snapshot.overall_average = Decimal("5.00")

        with self.assertRaises(ValidationError):
            snapshot.save()

    def test_publishing_a_snapshot_is_still_allowed(self):
        """Publishing is editorial, not a change to the measurement."""
        self.taught(weeks_ago=1)
        snapshot, _ = self.generate()
        snapshot.visible_to_family = True
        snapshot.save()

        snapshot.refresh_from_db()
        self.assertTrue(snapshot.visible_to_family)

    def test_a_snapshot_is_unpublished_unless_asked_for(self):
        self.taught(weeks_ago=1)
        snapshot, _ = self.generate()
        self.assertFalse(snapshot.visible_to_family)

    def test_the_summary_is_composed_from_the_numbers_by_default(self):
        self.taught(weeks_ago=1, score=4)
        snapshot, _ = self.generate()

        self.assertIn("1 of 1", snapshot.summary)
        self.assertIn("4.00", snapshot.summary)

    def test_the_lead_may_supply_their_own_summary(self):
        self.taught(weeks_ago=1)
        snapshot, _ = self.generate(summary="Ready to move up after Ramadan.")
        self.assertEqual(snapshot.summary, "Ready to move up after Ramadan.")

    def test_an_inverted_period_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            self.generate(
                period_start=self.period_end, period_end=self.period_start
            )
        self.assertIn("period_end", ctx.exception.message_dict)

    def test_only_a_student_has_progress_to_snapshot(self):
        with self.assertRaises(ValidationError) as ctx:
            self.generate(student=SubTeacherFactory())
        self.assertIn("student", ctx.exception.message_dict)

    def test_only_a_lead_generates_snapshots(self):
        with self.assertRaises(ValidationError) as ctx:
            self.generate(generated_by=SubTeacherFactory())
        self.assertIn("generated_by", ctx.exception.message_dict)

    def test_the_period_is_half_open_so_two_snapshots_never_share_a_session(self):
        boundary = dj_timezone.now() - timedelta(weeks=2)
        booking = past_completed_booking(
            availability=self.window, level=self.level, student=self.student
        )
        booking.start_time_utc = boundary
        booking.save()
        SessionAssessmentFactory(booking=booking)

        earlier, _ = self.generate(
            period_start=boundary - timedelta(weeks=4), period_end=boundary
        )
        later, _ = self.generate(
            period_start=boundary, period_end=boundary + timedelta(weeks=4)
        )

        self.assertEqual(earlier.assessed_sessions, 0)
        self.assertEqual(later.assessed_sessions, 1)

    def test_the_duplicate_rule_is_a_database_rule_too(self):
        self.taught(weeks_ago=1)
        self.generate()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProgressSnapshot.objects.bulk_create(
                    [
                        ProgressSnapshot(
                            student=self.student,
                            track=self.track,
                            period_start=self.period_start,
                            period_end=self.period_end,
                            completed_sessions=0,
                            assessed_sessions=0,
                        )
                    ]
                )


class AssessmentDoesNotTouchPlacementTests(TestCase):
    """Acceptance criterion 24, and CLAUDE.md's Phase 7 boundary."""

    def test_submitting_an_assessment_leaves_recommended_level_alone(self):
        placement = ReviewedPlacementFactory()
        level = placement.recommended_level
        window = AvailabilityFactory(teacher=BookableTeacherFactory())
        RubricWithCriteriaFactory(track=placement.track)
        booking = past_completed_booking(
            availability=window, level=level, student=placement.student
        )

        SessionAssessmentFactory(booking=booking, score_value=1)

        placement.refresh_from_db()
        self.assertEqual(placement.recommended_level, level)
        self.assertEqual(
            PlacementResult.objects.filter(student=placement.student).count(), 1
        )

    def test_a_perfect_score_does_not_promote_the_student_either(self):
        placement = ReviewedPlacementFactory()
        window = AvailabilityFactory(teacher=BookableTeacherFactory())
        RubricWithCriteriaFactory(track=placement.track)
        booking = past_completed_booking(
            availability=window,
            level=placement.recommended_level,
            student=placement.student,
        )

        SessionAssessmentFactory(booking=booking, score_value=5)

        placement.refresh_from_db()
        self.assertEqual(placement.recommended_level, booking.level)


class AssessmentTenancyModelTests(TestCase):
    """Model-level tenant invariant tests for SaaS Phase 6."""

    def test_assessment_rejects_student_not_in_organization(self):
        assessment = SessionAssessmentFactory()
        foreign_student = StudentFactory()  # not admitted to assessment.organization
        assessment.student = foreign_student
        with self.assertRaises(ValidationError) as ctx:
            assessment.clean()
        self.assertIn("student", ctx.exception.message_dict)

    def test_assessment_rejects_teacher_not_active_in_organization(self):
        assessment = SessionAssessmentFactory()
        foreign_teacher = BookableTeacherFactory()  # not admitted/configured in assessment.organization
        assessment.assessed_by = foreign_teacher
        with self.assertRaises(ValidationError) as ctx:
            assessment.clean()
        self.assertIn("assessed_by", ctx.exception.message_dict)

    def test_assessment_rejects_lead_reviewer_not_in_organization(self):
        assessment = FlaggedAssessmentFactory()
        foreign_lead = LeadTeacherFactory()
        with self.assertRaises(ValidationError) as ctx:
            assessment.record_lead_review(reviewed_by=foreign_lead)
        self.assertIn("lead_reviewed_by", ctx.exception.message_dict)

    def test_assessment_rejects_cross_academy_booking_and_track(self):
        assessment = SessionAssessmentFactory()
        foreign_track = TrackFactory()  # in a different organization
        assessment.track = foreign_track
        with self.assertRaises(ValidationError) as ctx:
            assessment.clean()
        self.assertIn("booking", ctx.exception.message_dict)

    def test_assessment_score_rejects_cross_academy_criterion(self):
        assessment = SessionAssessmentFactory()
        foreign_rubric = RubricWithCriteriaFactory()  # different organization
        foreign_criterion = foreign_rubric.criteria.first()
        score = AssessmentScore(
            assessment=assessment,
            criterion=foreign_criterion,
            score=Decimal("4.00"),
        )
        with self.assertRaises(ValidationError) as ctx:
            score.clean()
        self.assertIn("criterion", ctx.exception.message_dict)

    def test_progress_snapshot_rejects_student_not_in_organization(self):
        snapshot = ProgressSnapshotFactory()
        foreign_student = StudentFactory()
        snapshot.student = foreign_student
        with self.assertRaises(ValidationError) as ctx:
            snapshot.clean()
        self.assertIn("student", ctx.exception.message_dict)

    def test_progress_snapshot_rejects_generator_not_in_organization(self):
        snapshot = ProgressSnapshotFactory()
        foreign_user = StudentFactory()
        snapshot.generated_by = foreign_user
        with self.assertRaises(ValidationError) as ctx:
            snapshot.clean()
        self.assertIn("generated_by", ctx.exception.message_dict)

    def test_querysets_in_organization_filtering(self):
        org1 = OrganizationFactory()
        org2 = OrganizationFactory()

        track1 = TrackFactory(organization=org1)
        track2 = TrackFactory(organization=org2)

        rubric1 = RubricWithCriteriaFactory(track=track1)
        rubric2 = RubricWithCriteriaFactory(track=track2)

        self.assertIn(rubric1, AssessmentRubric.objects.in_organization(org1))
        self.assertNotIn(rubric2, AssessmentRubric.objects.in_organization(org1))
        self.assertIn(rubric2, AssessmentRubric.objects.in_organization(org2))
        self.assertNotIn(rubric1, AssessmentRubric.objects.in_organization(org2))

        booking1 = past_completed_booking(level=LevelFactory(track=track1))
        booking2 = past_completed_booking(level=LevelFactory(track=track2))
        assessment1 = SessionAssessmentFactory(booking=booking1)
        assessment2 = SessionAssessmentFactory(booking=booking2)

        self.assertIn(assessment1, SessionAssessment.objects.in_organization(org1))
        self.assertNotIn(assessment2, SessionAssessment.objects.in_organization(org1))
        self.assertIn(assessment2, SessionAssessment.objects.in_organization(org2))
        self.assertNotIn(assessment1, SessionAssessment.objects.in_organization(org2))

        snapshot1 = ProgressSnapshotFactory(track=track1, student=booking1.student)
        snapshot2 = ProgressSnapshotFactory(track=track2, student=booking2.student)
        self.assertIn(snapshot1, ProgressSnapshot.objects.in_organization(org1))
        self.assertNotIn(snapshot2, ProgressSnapshot.objects.in_organization(org1))
        self.assertIn(snapshot2, ProgressSnapshot.objects.in_organization(org2))
        self.assertNotIn(snapshot1, ProgressSnapshot.objects.in_organization(org2))

