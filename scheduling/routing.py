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

Phase 5 adds a fifth outcome that **replaces** the sequence rather than extending
it: when a request names a ``preferred_teacher``, steps 1-3 are skipped entirely
and only that teacher is tested. If they cannot take it *because they are full or
not free then*, the request becomes a ``TeacherWaitlist`` entry for that teacher
by name — never a redirect to somebody the family did not ask for, which is the
one thing mvp-spec section 4 insists on. See ``_route_to_preferred``.

Two things this module is careful about.

**It does not reimplement eligibility.** Every rule it needs — declared hours, no
clash, an approved teacher, the track specialty, the weekly cap, a start that is
not in the past — already lives in ``Booking.clean()``. So a candidate is tested
by building an unsaved ``Booking`` and asking the model. Routing and direct
booking therefore cannot drift apart, and a rule added to ``clean()`` later is
picked up here for free. Phase 5's preferred-teacher check and its waitlist
promotion are the same question asked about exactly one teacher, so they call the
same ``_candidate`` rather than growing a second copy of the rules (CLAUDE.md
asks for this specifically).

**It writes through ``Booking.save()``.** CLAUDE.md calls this out for both
phases: ``save()`` is what takes ``TeacherBookingLock``, so a ``bulk_create`` of
cohort seats or of promoted waitlist entries would bypass both the lock Phase 3.5
added and ``clean()`` entirely, silently reopening the race that phase closed.
"""

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import Role, User

from .exceptions import CohortFull, NoCapacity, WaitlistEntryAlreadyFulfilled
from .models import (
    Booking,
    Cohort,
    DEFAULT_DURATION_MINUTES,
    RoutedReason,
    TeacherWaitlist,
    remaining_weekly_minutes,
)

#: Refusal codes that mean "not now" rather than "not ever" — the only ones that
#: turn a preferred-teacher request into a waitlist entry.
#:
#: The spec's line is that capacity and availability refusals waitlist, while a
#: hard block (an unapproved profile, a specialty mismatch) "should still fail
#: outright". These three are the capacity-and-availability half of
#: ``Booking.clean()``: the teacher's week is full, their hours do not cover the
#: slot, or somebody else already has it. Every other code — a past start, a
#: track they do not teach, an unapproved or non-teacher account, a student who
#: cannot be booked at all — describes something that waiting will not fix, so
#: putting the family on a list for it would be a false promise.
WAITLISTABLE_CODES = frozenset(
    {
        "outside_availability",
        "teacher_weekly_capacity_exceeded",
        "teacher_double_booked",
    }
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


def as_validation_error(why_not):
    """Turn a ``refusals()`` dict back into a Django ``ValidationError``.

    The inverse of ``refusals``, used where routing has classified a refusal and
    decided the honest answer is the model's own complaint — a preferred teacher
    who does not teach the track, say. Rebuilding rather than keeping the original
    exception around means one representation of a refusal travels through this
    module, and codes survive the round trip so the view's 400 body reads exactly
    like a direct booking's.
    """
    return ValidationError(
        {
            field: [
                ValidationError(reason["message"], code=reason["code"])
                for reason in reasons
            ]
            for field, reasons in why_not.items()
        }
    )


def refusal_codes(why_not):
    """Every error code in a ``refusals()`` dict, flattened."""
    return {
        reason["code"] for reasons in why_not.values() for reason in reasons
    }


def is_waitlistable(why_not) -> bool:
    """Whether a refusal is "full or busy" rather than "never".

    Every code must be waitlistable, not merely one of them: a teacher who is both
    unapproved *and* full is refused for the first reason, and a waitlist entry
    would promise a slot that approving them is the only way to reach. An empty
    refusal is not waitlistable either — there is nothing to wait for.
    """
    codes = refusal_codes(why_not)
    return bool(codes) and codes <= WAITLISTABLE_CODES


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


def lead_teacher(organization=None, track=None):
    """The academy's lead teacher, or None if there isn't a bookable one.

    SaaS Phase 4: Lead candidate resolution is academy-scoped.
    Required candidate conditions:
    - User.role == Role.LEAD
    - active membership in organization
    - approved OrganizationTeacherConfiguration
    - active TeacherTrack for requested track (if track provided)
    """
    if organization is not None:
        from organizations.models import MembershipStatus

        qs = User.objects.filter(
            role=Role.LEAD,
            organization_memberships__organization=organization,
            organization_memberships__status=MembershipStatus.ACTIVE,
            organization_memberships__teacher_configuration__approved=True,
        )
        if track is not None:
            qs = qs.filter(
                organization_memberships__teacher_tracks__track=track,
                organization_memberships__teacher_tracks__active=True,
            )
        return qs.order_by("pk").distinct().first()

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


def matching_sub_teachers(level, organization=None):
    """Approved sub-teachers who specialise in ``level``'s track in ``organization``.

    SaaS Phase 4: Sub-teacher candidate resolution is academy-scoped using TeacherTrack.
    Candidate conditions:
    - User.role == Role.SUB
    - active organization membership
    - approved organization teacher configuration
    - active TeacherTrack for requested track
    """
    if organization is None and hasattr(level, "track") and hasattr(level.track, "organization"):
        organization = level.track.organization

    if organization is not None:
        from organizations.models import MembershipStatus

        return (
            User.objects.filter(
                role=Role.SUB,
                organization_memberships__organization=organization,
                organization_memberships__status=MembershipStatus.ACTIVE,
                organization_memberships__teacher_configuration__approved=True,
                organization_memberships__teacher_tracks__track=level.track,
                organization_memberships__teacher_tracks__active=True,
            )
            .distinct()
            .order_by("pk")
        )

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


def _waitlist(*, student, teacher, level, start_time_utc, duration_minutes, why_not):
    """Record the unmet request and raise the refusal that reports it.

    Both halves of what the spec asks for in one place, because they must not come
    apart: the entry is what makes the promise ("you are on this teacher's list")
    and the ``considered`` payload is what communicates it. ``considered`` keeps
    Phase 4's shape — a step name mapping usernames to refusals — and gains one
    extra key rather than a new response type, per learnings.md 2026-08-24.
    """
    entry = TeacherWaitlist.record(
        student=student,
        requested_teacher=teacher,
        level=level,
        requested_start_utc=start_time_utc,
        requested_duration_minutes=duration_minutes,
    )
    raise NoCapacity(
        f"{teacher.username} is not free for that slot. "
        f"{student.username} is on {teacher.username}'s waitlist for it — "
        "nobody else has been assigned.",
        considered={
            "preferred_teacher": {teacher.username: why_not},
            # The extension the spec names: the parent sees *which* list they are
            # on, not merely that their teacher was busy.
            "waitlist": {
                "id": entry.pk,
                "requested_teacher": teacher.username,
                "priority": entry.priority,
                "requested_at": entry.requested_at,
            },
        },
    )


def _resolve_preferred(*, student, teacher, level, start_time_utc, duration_minutes, organization=None):
    """The whole preferred-teacher path (Phase 5). Returns a ``Routed`` or raises.

    The spec's four steps, in order:

    1. Only this teacher is tested — the cohort → lead → sub sequence is skipped
       entirely, and no cohort is passed, so a preferred-teacher request always
       produces a **1:1 booking** even when the named teacher has an open cohort
       at the requested time. A parent naming a teacher wants that teacher, not a
       seat in a class; folding the two together is a distinct feature to design
       on purpose (logged in tech-debt.md).
    2. Eligible → save it, ``routed_reason=student_choice``, exactly as Phase 3
       already defined for a teacher named by the family.
    3. Refused for capacity or availability → a ``TeacherWaitlist`` entry, *not* a
       fall-through to a sub-teacher. Nobody gets silently redirected to someone
       they did not ask for, which is the whole point of the phase.
    4. Refused for anything else → fail outright, no entry. An unapproved teacher
       or a track they do not teach is not something waiting fixes.
    """
    booking, why_not = _candidate(
        student=student,
        teacher=teacher,
        level=level,
        start_time_utc=start_time_utc,
        duration_minutes=duration_minutes,
        reason=RoutedReason.STUDENT_CHOICE,
        # Deliberately no cohort: a preferred-teacher request is 1:1 by rule,
        # even against a group-eligible level this teacher runs a cohort for.
    )

    if booking is not None:
        try:
            booking.save()
        except ValidationError as exc:
            # Lost the slot between the candidate check and the write — the same
            # race Phase 4 documents, re-classified rather than propagated. A
            # teacher who filled up in those microseconds is exactly the case the
            # waitlist exists for, so it would be perverse to answer it with a
            # bare 400 when the identical refusal a moment earlier earns a place
            # in the queue.
            lost = refusals(exc) if hasattr(exc, "error_dict") else {}
            if not is_waitlistable(lost):
                raise
            _waitlist(
                student=student,
                teacher=teacher,
                level=level,
                start_time_utc=start_time_utc,
                duration_minutes=duration_minutes,
                why_not=lost,
            )
        return Routed(
            booking=booking,
            reason=RoutedReason.STUDENT_CHOICE,
            # Same key as the refusal uses, with nothing ruled out. A caller can
            # therefore read considered["preferred_teacher"] either way rather
            # than branching on which outcome it got.
            considered={"preferred_teacher": {teacher.username: {}}},
        )

    if is_waitlistable(why_not):
        _waitlist(
            student=student,
            teacher=teacher,
            level=level,
            start_time_utc=start_time_utc,
            duration_minutes=duration_minutes,
            why_not=why_not,
        )

    # A hard block. The model's own complaint is the honest answer, and it is the
    # answer a direct booking for the same pairing already gives (a 400 naming
    # the field), so it is raised in that shape rather than as a capacity
    # refusal: "she does not teach Hifz" is not something a queue fixes.
    raise as_validation_error(why_not)


def _route_to_cohort(*, student, level, start_time_utc, duration_minutes, organization=None):
    """Step 1. Returns ``((booking, cohort) | None, why_not)``.

    ``why_not`` is filled in whether or not the step succeeds, so a caller can
    report what was ruled out on the way to an answer as well as on the way to a
    refusal.
    """
    if not level.group_eligible:
        return None, f"{level} is not group-eligible, so it cannot run as a cohort."

    open_cohorts = Cohort.open_near(level, start_time_utc)
    if organization is not None:
        open_cohorts = [
            c for c in open_cohorts
            if getattr(c, "organization", None) == organization
        ]
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


def _route_to_lead(*, student, level, start_time_utc, duration_minutes, organization=None):
    """Step 2. Returns ``(booking | None, why_not)``."""
    if organization is None and hasattr(level, "track") and hasattr(level.track, "organization"):
        organization = level.track.organization
    track = getattr(level, "track", None)
    lead = lead_teacher(organization=organization, track=track)
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


def _route_to_sub(*, student, level, start_time_utc, duration_minutes, organization=None):
    """Step 3. Returns ``(booking | None, why_not)``.

    Every specialty match is tested, then the eligible ones are ranked by
    remaining weekly capacity — most free capacity wins, so work spreads instead
    of piling onto whichever teacher happens to sort first. Ties break on the
    lowest pk purely to be deterministic; there is no ranking beyond capacity,
    and the spec is explicit that adding one is a later phase's decision.

    The teachers who were ruled out come back even when one is chosen, so a caller
    can report who was considered rather than only who won.
    """
    if organization is None and hasattr(level, "track") and hasattr(level.track, "organization"):
        organization = level.track.organization
    subs = list(matching_sub_teachers(level, organization=organization))
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
                (
                    remaining_weekly_minutes(
                        sub, start_time_utc, organization=organization
                    ),
                    -sub.pk,
                    booking,
                )
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


def route_session(
    *,
    student,
    level,
    start_time_utc,
    duration_minutes=None,
    preferred_teacher=None,
    organization=None,
):
    """Assign a teacher for ``student`` at ``start_time_utc`` and book it.

    Returns a ``Routed`` describing what was decided. Raises ``NoCapacity`` when
    steps 1-3 all fail (or the preferred teacher cannot take it), carrying the
    per-step reasons — a clear failure is the correct outcome here, not a bug to
    route around.

    When ``preferred_teacher`` is given the normal cohort → lead → sub sequence
    is skipped entirely. Only that teacher is tested:

    * If they are eligible the booking is created as ``student_choice``.
    * If they cannot take it because of capacity or availability a
      ``TeacherWaitlist`` entry is created and returned inside ``NoCapacity`` —
      the parent is never silently redirected to someone they did not ask for
      (mvp-spec section 4). The response extends ``NoCapacity.considered`` with
      the waitlist entry id, per learnings.md 2026-08-24.
    * If they have a hard block (wrong track, unapproved, past slot) the request
      fails outright with no waitlist.

    A preferred-teacher request is always 1:1. Even if the named teacher has an
    open cohort at the requested time, they are booked directly — a parent naming
    a teacher wants that teacher, not a seat in a group class. This scope
    limitation is logged in tech-debt.md.

    The write goes through ``Booking.save()``, so it takes the teacher's lock and
    re-runs every rule under it. That matters: the candidate check above ran
    against a database that may have changed since. A booking that loses that
    race raises ``ValidationError`` exactly as a direct booking would, rather
    than being quietly reassigned to a teacher the student was never matched
    with.
    """
    if (
        organization is None
        and hasattr(level, "track")
        and hasattr(level.track, "organization")
    ):
        organization = level.track.organization

    if (
        organization is not None
        and hasattr(level, "track")
        and hasattr(level.track, "organization")
        and level.track.organization is not None
        and level.track.organization != organization
    ):
        raise ValidationError(
            {"level": [f"{level} does not belong to {getattr(organization, 'name', organization)}."]}
        )

    duration_minutes = duration_minutes or DEFAULT_DURATION_MINUTES
    considered = {}
    common = {
        "student": student,
        "level": level,
        "start_time_utc": start_time_utc,
        "duration_minutes": duration_minutes,
        "organization": organization,
    }

    if preferred_teacher is not None:
        return _resolve_preferred(teacher=preferred_teacher, **common)

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


def promote_waitlist_entry(entry, *, start_time_utc=None, duration_minutes=None):
    """Turn an open ``TeacherWaitlist`` entry into the session it was waiting for.

    This is the whole fulfillment mechanism this phase ships: the lead reads
    ``/waitlist/for-teacher/?teacher_id=``, picks an entry, and promotes it.
    Automatic offering when a slot frees up is explicitly out of scope (spec, and
    tech-debt.md) — it needs background jobs and notification delivery, which is a
    phase of its own.

    The slot defaults to the one the family originally asked for, and
    ``start_time_utc`` / ``duration_minutes`` override it — a lead offering the
    10:00 that just opened rather than the 09:00 nobody has. The entry keeps its
    original request either way, so what was wanted stays distinguishable from
    what was given.

    Two rules CLAUDE.md names for this function specifically, both load-bearing:

    * The eligibility check is ``_candidate`` — the same unsaved-``Booking``
      mechanism routing uses, so promotion holds no copy of the rules and cannot
      drift from them. An entry made last week against a teacher who has since
      filled up, narrowed their hours or lost their approval is refused here.
    * The write is ``Booking.save()``, which takes ``TeacherBookingLock`` and
      re-runs every rule under it. No ``bulk_create``, no hand-built row: a
      candidate that loses its slot to a concurrent write between the check and
      the write is refused rather than forced.

    Raises ``WaitlistEntryAlreadyFulfilled`` if the entry already has a booking,
    and ``ValidationError`` if the teacher is no longer eligible — an honest
    refusal, exactly as the routing engine gives.
    """
    if not entry.is_open:
        raise WaitlistEntryAlreadyFulfilled(
            f"This request was already fulfilled by booking "
            f"{entry.fulfilled_booking_id}; promoting it again would either "
            "overwrite that record or double-book the family."
        )

    booking, why_not = _candidate(
        student=entry.student,
        teacher=entry.requested_teacher,
        level=entry.level,
        start_time_utc=start_time_utc or entry.requested_start_utc,
        duration_minutes=duration_minutes or entry.requested_duration_minutes,
        # The family named this teacher; that is what student_choice means, and it
        # is what the refused request would have recorded had it succeeded.
        reason=RoutedReason.STUDENT_CHOICE,
    )
    if why_not is None:
        # One transaction, so an entry is never stamped without its booking or a
        # booking left behind by a failed stamp. Booking.save() takes the
        # teacher's lock inside this block and holds it to the outer commit.
        with transaction.atomic():
            booking.save()
            entry.mark_fulfilled(booking)
        return Routed(
            booking=booking,
            reason=RoutedReason.STUDENT_CHOICE,
            considered={"waitlist": {"id": entry.pk, "promoted": True}},
        )

    # No waitlist branch here, unlike the routing path: the family is *already* on
    # this list. Refusing tells the lead the slot is no longer available, and the
    # entry stays open for the next attempt.
    raise as_validation_error(why_not)
