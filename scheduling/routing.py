"""Phase 4's routing engine: given a student, a level and a slot, decide who teaches.

This is the answer to the problem the whole product exists for — the lead
teacher's time is the bottleneck, and a human picking a teacher for every request
is what makes that true. The order of resolution comes from
specs/phase-4-routing.md and mvp-spec section 2:

1. **Cohort first**, if the level is group-eligible and an open cohort starts
   near the requested time. One teacher covering six students is worth more than
   any 1:1 cleverness.
2. **The lead teacher**, if they have declared hours covering the slot and are
   under their weekly cap.
3. **A matched sub-teacher** — specialises in the level's track, free then, under
   their own cap. Among those, whoever has the most remaining weekly capacity, so
   load spreads. Deliberately not a weighted score; the spec says resist that.
4. **Nothing.** ``NoCapacity`` is raised, carrying the reason each step failed.
   Booking someone outside their hours or over their cap to avoid an error would
   defeat the point of the phase.

Two things this module is careful about.

**It does not reimplement eligibility.** Every rule it needs — declared hours, no
clash, an approved teacher, the track specialty, the weekly cap, a start that is
not in the past — already lives in ``Booking.clean()``. So a candidate is tested
by building an unsaved ``Booking`` and asking the model. Routing and direct
booking therefore cannot drift apart, and a rule added to ``clean()`` later is
picked up here for free.

**It writes through ``Booking.save()``.** CLAUDE.md calls this out for the phase:
``save()`` is what takes ``TeacherBookingLock``, so a ``bulk_create`` of cohort
seats would bypass both the lock Phase 3.5 added and ``clean()`` entirely,
silently reopening the race that phase closed.
"""

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import Role, User

from .exceptions import CohortFull, NoCapacity
from .models import (
    Booking,
    Cohort,
    DEFAULT_DURATION_MINUTES,
    RoutedReason,
    remaining_weekly_minutes,
)


@dataclass
class Routed:
    """What routing decided, and what it had to rule out to get there."""

    booking: Booking
    reason: str
    cohort: Cohort | None = None
    considered: dict = field(default_factory=dict)


def refusals(exc):
    """A ``{field: [{code, message}]}`` view of a ValidationError.

    Both halves are kept deliberately. The message is what a frontend shows a
    parent; the code is what a frontend (or a test) can branch on without
    matching prose that is free to be reworded. Non-field errors arrive under
    Django's ``__all__`` key, which is what a client sees elsewhere in this API
    too, so it is left alone rather than renamed into something prettier and less
    greppable.
    """
    return {
        field: [
            {"code": getattr(error, "code", None), "message": message}
            for error in errors
            for message in error.messages
        ]
        for field, errors in exc.error_dict.items()
    }


def _candidate(*, student, teacher, level, start_time_utc, duration_minutes, reason, cohort=None):
    """An unsaved booking, plus why the model refuses it — ``None`` if it doesn't.

    The single point where routing asks "could this person take this session".
    Nothing here knows *what* the rules are, which is the point.
    """
    booking = Booking(
        student=student,
        teacher=teacher,
        level=level,
        cohort=cohort,
        start_time_utc=start_time_utc,
        duration_minutes=duration_minutes,
        routed_reason=reason,
    )
    try:
        booking.full_clean()
    except ValidationError as exc:
        return None, refusals(exc)
    return booking, None


def lead_teacher():
    """The academy's lead teacher, or None if there isn't a bookable one.

    ``role`` and ``TeacherProfile.is_lead`` duplicate each other and are
    validated to agree (learnings.md), so filtering on both is belt-and-braces
    rather than two conditions. Ordered by pk because nothing yet stops a second
    lead existing — that is a known gap in tech-debt.md, and routing picking
    arbitrarily would make it a silent one.
    """
    return (
        User.objects.filter(
            role=Role.LEAD,
            teacher_profile__is_lead=True,
            teacher_profile__approved=True,
        )
        .select_related("teacher_profile")
        .order_by("pk")
        .first()
    )


def matching_sub_teachers(level):
    """Approved sub-teachers who specialise in ``level``'s track.

    Specialty is filtered in SQL because it is the cheap discriminator; hours,
    clashes and capacity are per-candidate and are left to ``_candidate``.
    """
    return (
        User.objects.filter(
            role=Role.SUB,
            teacher_profile__approved=True,
            teacher_profile__specialties=level.track,
        )
        .select_related("teacher_profile")
        .distinct()
        .order_by("pk")
    )


def _route_to_cohort(*, student, level, start_time_utc, duration_minutes):
    """Step 1. Returns ``((booking, cohort) | None, why_not)``.

    ``why_not`` is filled in whether or not the step succeeds, so a caller can
    report what was ruled out on the way to an answer as well as on the way to a
    refusal.
    """
    if not level.group_eligible:
        return None, f"{level} is not group-eligible, so it cannot run as a cohort."

    open_cohorts = Cohort.open_near(level, start_time_utc)
    if not open_cohorts:
        return None, "No open cohort for that level starts near the requested time."

    rejected = {}
    for cohort in open_cohorts:
        # A student already seated here is already assigned; giving them a second
        # seat in the same class is not a routing outcome. Same-cohort seats are
        # exempt from the overlap rule, so nothing else would catch it.
        if cohort.students.filter(pk=student.pk).exists():
            rejected[str(cohort)] = [
                {
                    "code": "already_seated",
                    "message": "Student already has a seat in this cohort.",
                }
            ]
            continue
        seat_error = cohort.seat_error(student)
        if seat_error is not None:
            rejected[str(cohort)] = [
                {
                    "code": seat_error.code,
                    "message": seat_error.message % seat_error.params,
                }
            ]
            continue
        booking, why_not = _candidate(
            student=student,
            teacher=cohort.teacher,
            level=cohort.level,
            # A seat *is* the cohort's session: its time is the cohort's, not the
            # requested one, which is what "reasonably close" tolerates.
            start_time_utc=cohort.schedule_start_utc,
            duration_minutes=duration_minutes,
            reason=RoutedReason.COHORT_ASSIGNED,
            cohort=cohort,
        )
        if why_not is None:
            return (booking, cohort), rejected
        rejected[str(cohort)] = why_not
    return None, rejected


def _route_to_lead(*, student, level, start_time_utc, duration_minutes):
    """Step 2. Returns ``(booking | None, why_not)``."""
    lead = lead_teacher()
    if lead is None:
        return None, "No approved lead teacher exists."
    booking, why_not = _candidate(
        student=student,
        teacher=lead,
        level=level,
        start_time_utc=start_time_utc,
        duration_minutes=duration_minutes,
        reason=RoutedReason.LEAD_AVAILABLE,
    )
    return booking, ({} if why_not is None else {lead.username: why_not})


def _route_to_sub(*, student, level, start_time_utc, duration_minutes):
    """Step 3. Returns ``(booking | None, why_not)``.

    Every specialty match is tested, then the eligible ones are ranked by
    remaining weekly capacity — most free capacity wins, so work spreads instead
    of piling onto whichever teacher happens to sort first. Ties break on the
    lowest pk purely to be deterministic; there is no ranking beyond capacity,
    and the spec is explicit that adding one is a later phase's decision.

    The teachers who were ruled out come back even when one is chosen, so a caller
    can report who was considered rather than only who won.
    """
    subs = list(matching_sub_teachers(level))
    if not subs:
        return None, f"No approved sub-teacher specialises in {level.track.name}."

    eligible = []
    rejected = {}
    for sub in subs:
        booking, why_not = _candidate(
            student=student,
            teacher=sub,
            level=level,
            start_time_utc=start_time_utc,
            duration_minutes=duration_minutes,
            reason=RoutedReason.LEAD_FULL_ROUTED,
        )
        if why_not is None:
            eligible.append(
                (remaining_weekly_minutes(sub, start_time_utc), -sub.pk, booking)
            )
        else:
            rejected[sub.username] = why_not

    if not eligible:
        return None, rejected
    # Most remaining capacity first; -pk under reverse=True means lowest pk wins
    # a tie. Booking objects are never compared — the two keys before it are
    # always distinct per candidate.
    eligible.sort(key=lambda candidate: candidate[:2], reverse=True)
    return eligible[0][2], rejected


def route_session(*, student, level, start_time_utc, duration_minutes=None):
    """Assign a teacher for ``student`` at ``start_time_utc`` and book it.

    Returns a ``Routed`` describing what was decided. Raises ``NoCapacity`` when
    steps 1-3 all fail, carrying the per-step reasons — a clear failure is the
    correct outcome here, not a bug to route around.

    The write goes through ``Booking.save()``, so it takes the teacher's lock and
    re-runs every rule under it. That matters: the candidate check above ran
    against a database that may have changed since. A booking that loses that
    race raises ``ValidationError`` exactly as a direct booking would, rather
    than being quietly reassigned to a teacher the student was never matched
    with.
    """
    duration_minutes = duration_minutes or DEFAULT_DURATION_MINUTES
    considered = {}
    common = {
        "student": student,
        "level": level,
        "start_time_utc": start_time_utc,
        "duration_minutes": duration_minutes,
    }

    seat, considered["cohort"] = _route_to_cohort(**common)
    if seat is not None:
        booking, cohort = seat
        # One transaction, so a seat is never given without its session or the
        # other way round. Booking.save() takes the teacher's lock inside this
        # block and holds it until the outer commit, which also serialises the
        # last-seat check against another request for the same cohort.
        with transaction.atomic():
            booking.save()
            try:
                cohort.add_student(student)
            except CohortFull as exc:
                # Lost the last seat between the check and here. Surface it as a
                # validation failure, the same shape a lost booking race has.
                raise ValidationError({"cohort": [str(exc)]}) from exc
        return Routed(
            booking=booking,
            reason=RoutedReason.COHORT_ASSIGNED,
            cohort=cohort,
            considered=considered,
        )

    booking, considered["lead"] = _route_to_lead(**common)
    if booking is not None:
        booking.save()
        return Routed(
            booking=booking,
            reason=RoutedReason.LEAD_AVAILABLE,
            considered=considered,
        )

    booking, considered["sub_teachers"] = _route_to_sub(**common)
    if booking is not None:
        booking.save()
        return Routed(
            booking=booking,
            reason=RoutedReason.LEAD_FULL_ROUTED,
            considered=considered,
        )

    raise NoCapacity(
        "No cohort, no lead-teacher capacity and no matching sub-teacher for "
        "that level at that time.",
        considered=considered,
    )
