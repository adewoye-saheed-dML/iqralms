"""factory_boy factories for the assessment app.

Per CLAUDE.md every model gets a factory here before tests are written against
it. Three of them derive values rather than hardcoding them, so a factory can
never build a state the model would reject:

* ``AssessmentCriterionFactory.order`` asks the rubric what position is free, the
  way ``LevelFactory.order`` asks the track — the order is unique within a rubric,
  so a hardcoded 1 would break the second criterion.
* ``SessionAssessmentFactory`` goes through ``SessionAssessment.submit`` rather
  than constructing a row, because submitting *is* the only legal creation path:
  it is what resolves the active rubric, checks the criterion set is complete and
  writes the snapshots. It also builds a rubric for the booking's track if none
  exists, since an assessment against a track with no rubric is not a state to
  build by accident. A test that wants that rejection calls ``submit`` itself.
* ``ProgressSnapshotFactory`` goes through ``ProgressSnapshot.generate``, for the
  same reason: every number on a snapshot is computed, so a factory that typed
  them in would be asserting fiction.

``past_completed_booking`` is the other thing worth knowing about. Period-scoped
tests (reports, progress, snapshots) need sessions that already happened, and a
*completed* booking may be past-dated: ``Booking.clean()`` applies its time rules
only while a booking is ``scheduled``, precisely so history stays saveable.
"""

from datetime import timedelta

import factory
from django.utils import timezone as dj_timezone

from accounts.tests.factories import LeadTeacherFactory, StudentFactory
from assessment.models import (
    AssessmentCriterion,
    AssessmentRubric,
    ProgressSnapshot,
    SessionAssessment,
)
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableTeacherFactory,
    CompletedBookingFactory,
    ensure_teacher_configured,
)

#: The score a factory-built assessment gives every criterion unless told
#: otherwise. 4 rather than 3 so "the default" is distinguishable from "the
#: middle of the scale" in an assertion.
DEFAULT_SCORE = 4

#: A plausible tajweed sheet. Content is data owned by the lead, so these are
#: fixtures, not defaults the application knows about.
DEFAULT_CRITERION_NAMES = ("Makhraj accuracy", "Madd rules", "Fluency")


class AssessmentRubricFactory(factory.django.DjangoModelFactory):
    """An active rubric with no criteria yet."""

    class Meta:
        model = AssessmentRubric

    track = factory.SubFactory(TrackFactory)
    name = factory.Sequence(lambda n: f"Rubric {n}")
    description = "Shared scoring sheet for this track."
    active = True


def next_criterion_order(rubric) -> int:
    """The next free position on ``rubric``'s sheet."""
    taken = AssessmentCriterion.objects.filter(rubric=rubric).values_list(
        "order", flat=True
    )
    return max(taken, default=0) + 1


class AssessmentCriterionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AssessmentCriterion

    rubric = factory.SubFactory(AssessmentRubricFactory)
    name = factory.Sequence(lambda n: f"Criterion {n}")
    description = ""
    # Positions are unique within a rubric, so ask what is free rather than
    # guessing — the same reason LevelFactory derives its order.
    order = factory.LazyAttribute(lambda o: next_criterion_order(o.rubric))
    active = True


class RubricWithCriteriaFactory(AssessmentRubricFactory):
    """A rubric that already has a usable sheet — the normal starting point.

    ``RubricWithCriteriaFactory(criteria=1)`` builds a single-criterion sheet;
    ``criteria=0`` builds none, which is the case an assessment cannot be
    submitted against.
    """

    @factory.post_generation
    def criteria(obj, create, extracted, **kwargs):
        if not create:
            return
        count = len(DEFAULT_CRITERION_NAMES) if extracted is None else extracted
        for index in range(count):
            name = (
                DEFAULT_CRITERION_NAMES[index]
                if index < len(DEFAULT_CRITERION_NAMES)
                else f"Criterion {index + 1}"
            )
            AssessmentCriterionFactory(rubric=obj, name=name)


def rubric_for(track):
    """The track's active rubric, building a default sheet if it has none."""
    return AssessmentRubric.active_for_track(track) or RubricWithCriteriaFactory(
        track=track
    )


def past_completed_booking(*, weeks_ago=1, **kwargs):
    """A completed session that happened ``weeks_ago``.

    Past-dating is legitimate here and only here: ``Booking.clean()`` runs its
    time-based rules — not in the past, inside declared hours, within the weekly
    cap — only while a booking is ``scheduled``, so that a session that has
    already been taught stays saveable. A *scheduled* booking in the past is
    still refused, which is the rule Phase 3.5 added.
    """
    kwargs.setdefault(
        "start_time_utc", dj_timezone.now() - timedelta(weeks=weeks_ago)
    )
    return CompletedBookingFactory(**kwargs)


class SessionAssessmentFactory(factory.django.DjangoModelFactory):
    """A submitted assessment scoring every active criterion of its track's rubric.

    ``SessionAssessmentFactory(booking=b)`` assesses ``b`` as its own teacher.
    ``score_value=`` changes the mark given to every criterion; ``scores=`` takes
    full control of the payload, which is what the missing/duplicate/out-of-range
    tests use.
    """

    class Meta:
        model = SessionAssessment

    # A past session, not a future one: "completed" and "has not happened yet"
    # are contradictory, and a default in the future would quietly fall outside
    # every period-scoped test's window.
    booking = factory.LazyFunction(past_completed_booking)
    teacher_summary = "Steady progress; keep revising the madd rules."
    flagged_for_review = False
    flag_reason = ""

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Submit rather than construct — see the module docstring.

        It cannot be a ``post_generation`` hook: those run after the row exists,
        and here the row *is* what submitting produces.
        """
        booking = kwargs.pop("booking")
        rubric = kwargs.pop("rubric", None) or rubric_for(booking.level.track)
        org = booking.level.track.organization if booking.level_id and booking.level.track_id else None
        if org is not None:
            if booking.student and not kwargs.get("_skip_admit_student"):
                admit(booking.student, org)
            assessor = kwargs.get("assessed_by") or booking.teacher
            if assessor and not kwargs.get("_skip_admit_teacher"):
                admit(assessor, org)
                ensure_teacher_configured(assessor, org)
        kwargs.pop("_skip_admit_student", None)
        kwargs.pop("_skip_admit_teacher", None)

        score_value = kwargs.pop("score_value", DEFAULT_SCORE)
        scores = kwargs.pop("scores", None)
        if scores is None:
            scores = [
                {"criterion": criterion, "score": score_value}
                for criterion in rubric.active_criteria()
            ]
        return model_class.submit(
            booking=booking,
            assessed_by=kwargs.pop("assessed_by", None) or booking.teacher,
            scores=scores,
            teacher_summary=kwargs.pop("teacher_summary", ""),
            flagged_for_review=kwargs.pop("flagged_for_review", False),
            flag_reason=kwargs.pop("flag_reason", ""),
        )


class FlaggedAssessmentFactory(SessionAssessmentFactory):
    """Flagged by the teacher, not yet reviewed — the lead's queue, populated."""

    flagged_for_review = True
    flag_reason = "Student could not hold the madd; want a second opinion."


class ReviewedAssessmentFactory(FlaggedAssessmentFactory):
    """Flagged and then reviewed, so it has left the pending queue.

    The review is stamped after creation on purpose: ``record_lead_review`` is the
    only path that writes those three fields, and going through it here means the
    factory cannot produce a review state the model would refuse.
    """

    @factory.post_generation
    def lead_review(obj, create, extracted, **kwargs):
        if not create:
            return
        reviewer = extracted or LeadTeacherFactory()
        if obj.organization is not None and not kwargs.get("_skip_admit_reviewer"):
            admit(reviewer, obj.organization)
        obj.record_lead_review(
            reviewed_by=reviewer,
            note=kwargs.get("note", "Agreed — worth another week on madd."),
        )


class ProgressSnapshotFactory(factory.django.DjangoModelFactory):
    """A generated snapshot over the last four weeks, unpublished by default."""

    class Meta:
        model = ProgressSnapshot

    student = factory.SubFactory(StudentFactory)
    track = factory.SubFactory(TrackFactory)
    period_start = factory.LazyFunction(
        lambda: dj_timezone.now() - timedelta(weeks=4)
    )
    period_end = factory.LazyFunction(dj_timezone.now)
    generated_by = factory.SubFactory(LeadTeacherFactory)
    visible_to_family = False

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        student = kwargs["student"]
        track = kwargs["track"]
        generated_by = kwargs.get("generated_by")
        org = track.organization if track and hasattr(track, "organization") else None
        if org is not None:
            if student and not kwargs.get("_skip_admit_student"):
                admit(student, org)
            if generated_by and not kwargs.get("_skip_admit_generator"):
                admit(generated_by, org)
        kwargs.pop("_skip_admit_student", None)
        kwargs.pop("_skip_admit_generator", None)

        # generate() computes every number and refuses to duplicate a period.
        snapshot, _ = model_class.generate(
            student=student,
            track=track,
            period_start=kwargs["period_start"],
            period_end=kwargs["period_end"],
            generated_by=generated_by,
            summary=kwargs.get("summary", ""),
            visible_to_family=kwargs.get("visible_to_family", False),
        )
        return snapshot


class PublishedSnapshotFactory(ProgressSnapshotFactory):
    """Released to the family — the only kind a student or parent can read."""

    visible_to_family = True


def assessed_teacher_world(*, level=None, weeks_ago=1, student=None):
    """A teacher, a completed session of theirs, and its assessment.

    The shape most tests want in one call: an approved sub-teacher with declared
    hours, a past completed booking, a rubric for that level's track, and a
    submitted assessment. Returns ``(assessment, booking)``.
    """
    level = level or LevelFactory()
    org = level.track.organization
    window = AvailabilityFactory(
        teacher=BookableTeacherFactory(),
        organization=org,
    )
    booking = past_completed_booking(
        availability=window,
        level=level,
        weeks_ago=weeks_ago,
        **({"student": student} if student is not None else {}),
    )
    return SessionAssessmentFactory(booking=booking), booking
