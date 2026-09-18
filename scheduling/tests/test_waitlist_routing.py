"""Phase 5's preferred-teacher routing, at the model layer.

Acceptance criteria from specs/phase-5-pricing-waitlist.md covered here:

* **4** — a preferred teacher with capacity books directly with
  ``routed_reason=student_choice`` and no waitlist entry.
* **5** — a preferred teacher at capacity produces a ``TeacherWaitlist`` entry and
  a refusal that carries it in ``considered``, not a bare ``NoCapacity``.
* **6** — a preferred-teacher request against a group-eligible level still
  produces a 1:1 booking, never a cohort seat, even when that teacher has an open
  cohort at the right time.
* **7** and **8** — promotion goes through ``Booking.save()`` and stamps
  ``fulfilled_booking`` with the row still present; promoting an entry whose
  teacher is no longer eligible fails cleanly rather than forcing the booking.

The HTTP surface for all of it is in test_waitlist_api.py. Both layers matter for
the reason Phase 3 set out: the rules live in ``clean()`` so they hold for direct
ORM writes, and the API tests prove they surface as 400s and 409s.

``RoutingWorld`` is reused from test_routing.py rather than rebuilt — routing's
answer is a function of the whole cast, and a second copy of the world-building
would eventually disagree with the first about what "available" means.
"""

from datetime import time, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.tests.factories import MinorStudentFactory, StudentFactory
from curriculum.tests.factories import (
    GroupEligibleLevelFactory,
    LevelFactory,
    TrackFactory,
    admit,
)
from scheduling.exceptions import NoCapacity, WaitlistEntryAlreadyFulfilled
from scheduling.models import (
    Availability,
    Booking,
    BookingStatus,
    RoutedReason,
    TeacherBookingLock,
    TeacherWaitlist,
)
from scheduling.routing import promote_waitlist_entry, route_session

from .factories import CohortFactory, teaches
from .test_routing import RoutingWorld, next_monday, refusal_codes


def error_codes(exc):
    """Every error ``code`` in a Django ``ValidationError``, flattened and sorted.

    The counterpart to ``refusal_codes`` for the paths that *raise* rather than
    report. Codes rather than messages for the reason test_routing.py gives: several
    rules can refuse the same booking, and prose is free to be reworded — while
    ``str(exc)`` renders only the messages, so a test grepping it would pass on a
    refusal from the wrong rule.
    """
    return sorted(
        getattr(error, "code", None)
        for errors in exc.error_dict.values()
        for error in errors
    )


class PreferredTeacherWorld(RoutingWorld):
    """``RoutingWorld`` plus the one call this phase adds."""

    def setUp(self):
        self.level = LevelFactory()
        self.student = StudentFactory()
        if hasattr(self.level, "track") and self.level.track.organization:
            admit(self.student, self.level.track.organization)
        self.slot = next_monday() + timedelta(hours=10)

    def prefer(self, teacher, **overrides):
        """Route naming ``teacher``, everything else defaulted."""
        return self.route(preferred_teacher=teacher, **overrides)


class PreferredTeacherWithCapacityTests(PreferredTeacherWorld, TestCase):
    """Acceptance criterion 4 — the happy path books directly, and only directly."""

    def test_a_preferred_teacher_with_capacity_is_booked(self):
        """Acceptance criterion 4."""
        wanted = self.available_teacher()

        routed = self.prefer(wanted)

        self.assertEqual(routed.booking.teacher, wanted)
        self.assertEqual(routed.reason, RoutedReason.STUDENT_CHOICE)
        self.assertEqual(routed.booking.routed_reason, RoutedReason.STUDENT_CHOICE)
        self.assertEqual(routed.booking.status, BookingStatus.SCHEDULED)
        self.assertTrue(routed.booking.pk)

    def test_no_waitlist_entry_is_created_when_the_booking_succeeds(self):
        """The other half of criterion 4: a satisfied request is not a waiting one."""
        self.prefer(self.available_teacher())
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_the_lead_can_be_asked_for_by_name(self):
        """The spec's central case: "I want you specifically"."""
        lead = self.available_teacher(lead=True)
        routed = self.prefer(lead)

        self.assertEqual(routed.booking.teacher, lead)
        self.assertEqual(routed.reason, RoutedReason.STUDENT_CHOICE)

    def test_a_sub_teacher_can_be_asked_for_by_name(self):
        """Nothing restricts the preference to the lead — a family may want anyone."""
        self.available_teacher(lead=True)
        sub = self.available_teacher()

        routed = self.prefer(sub)
        self.assertEqual(routed.booking.teacher, sub)

    def test_the_requested_slot_is_honoured_exactly(self):
        """Routing decides who, never when — Phase 4's rule, unchanged here."""
        wanted = self.available_teacher()
        routed = self.prefer(wanted)
        self.assertEqual(routed.booking.start_time_utc, self.slot)

    def test_a_preferred_booking_gets_a_video_room_like_any_other(self):
        routed = self.prefer(self.available_teacher())
        self.assertTrue(routed.booking.video_provider_meeting_id)
        self.assertIn(routed.booking.video_provider_meeting_id, routed.booking.video_join_url)

    def test_the_lead_is_not_booked_when_somebody_else_was_asked_for(self):
        """A preference overrides the routing order, not merely supplements it."""
        lead = self.available_teacher(lead=True)
        sub = self.available_teacher()

        routed = self.prefer(sub)

        self.assertEqual(routed.booking.teacher, sub)
        self.assertEqual(Booking.objects.filter(teacher=lead).count(), 0)

    def test_a_parent_can_name_a_teacher_for_a_linked_child(self):
        from accounts.tests.factories import ParentLinkFactory

        link = ParentLinkFactory()
        wanted = self.available_teacher()

        routed = self.prefer(wanted, student=link.student)

        self.assertEqual(routed.booking.student, link.student)
        self.assertEqual(routed.booking.teacher, wanted)

    def test_the_considered_payload_names_the_teacher_who_was_asked_for(self):
        """One key to read either way, so a caller need not branch on the outcome."""
        wanted = self.available_teacher()
        routed = self.prefer(wanted)

        self.assertIn("preferred_teacher", routed.considered)
        self.assertEqual(list(routed.considered["preferred_teacher"]), [wanted.username])

    def test_the_normal_routing_steps_are_not_reported(self):
        """They did not run, so claiming they declined would be a lie."""
        routed = self.prefer(self.available_teacher())

        self.assertNotIn("cohort", routed.considered)
        self.assertNotIn("lead", routed.considered)
        self.assertNotIn("sub_teachers", routed.considered)


class PreferredTeacherAtCapacityTests(PreferredTeacherWorld, TestCase):
    """Acceptance criterion 5 — full means waitlisted, never redirected."""

    def test_a_full_preferred_teacher_produces_a_waitlist_entry(self):
        """Acceptance criterion 5, first half."""
        wanted = self.available_teacher(hours=1)
        self.fill_week(wanted, 60)

        with self.assertRaises(NoCapacity):
            self.prefer(wanted)

        entry = TeacherWaitlist.objects.get()
        self.assertEqual(entry.student, self.student)
        self.assertEqual(entry.requested_teacher, wanted)
        self.assertEqual(entry.level, self.level)
        self.assertEqual(entry.requested_start_utc, self.slot)
        self.assertTrue(entry.is_open)

    def test_the_refusal_carries_the_waitlist_entry(self):
        """Acceptance criterion 5, second half — not a generic NoCapacity.

        The spec: "the response includes it in ``considered`` — not a generic
        ``NoCapacity`` with no trace of the preference".
        """
        wanted = self.available_teacher(hours=1)
        self.fill_week(wanted, 60)

        with self.assertRaises(NoCapacity) as ctx:
            self.prefer(wanted)

        entry = TeacherWaitlist.objects.get()
        considered = ctx.exception.considered

        self.assertEqual(considered["waitlist"]["id"], entry.pk)
        self.assertEqual(considered["waitlist"]["requested_teacher"], wanted.username)
        self.assertIn(wanted.username, considered["preferred_teacher"])

    def test_the_refusal_says_why_the_teacher_declined(self):
        """What makes the refusal showable rather than a bare error."""
        wanted = self.available_teacher(hours=1)
        self.fill_week(wanted, 60)

        with self.assertRaises(NoCapacity) as ctx:
            self.prefer(wanted)

        self.assertEqual(
            refusal_codes(ctx.exception.considered["preferred_teacher"]),
            ["teacher_weekly_capacity_exceeded"],
        )

    def test_nobody_else_is_assigned(self):
        """The one case where routing must *not* fall through to a sub-teacher.

        mvp-spec section 4: "not auto-routed to a sub-teacher... not quietly
        redirected to someone they didn't ask for". A free lead and a free sub both
        exist here and both are deliberately left alone.
        """
        wanted = self.available_teacher(hours=1)
        self.fill_week(wanted, 60)
        free_lead = self.available_teacher(lead=True)
        free_sub = self.available_teacher()

        with self.assertRaises(NoCapacity):
            self.prefer(wanted)

        self.assertFalse(Booking.objects.filter(start_time_utc=self.slot).exists())
        self.assertEqual(Booking.objects.filter(teacher=free_lead).count(), 0)
        self.assertEqual(Booking.objects.filter(teacher=free_sub).count(), 0)

    def test_a_teacher_outside_their_declared_hours_is_waitlisted(self):
        """"Not free then" is as waitlistable as "full" — both pass with time."""
        wanted = self.make_teacher()
        Availability.objects.create(
            teacher=wanted,
            organization=self.level.track.organization,
            weekday=self.slot.weekday(),
            start_time_utc=time(3, 0),
            end_time_utc=time(4, 0),
        )

        with self.assertRaises(NoCapacity) as ctx:
            self.prefer(wanted)

        self.assertTrue(TeacherWaitlist.objects.exists())
        self.assertEqual(
            refusal_codes(ctx.exception.considered["preferred_teacher"]),
            ["outside_availability"],
        )

    def test_a_teacher_already_booked_at_that_time_is_waitlisted(self):
        wanted = self.available_teacher()
        self.book(
            teacher=wanted,
            start_time_utc=self.slot,
            duration_minutes=30,
        )

        with self.assertRaises(NoCapacity) as ctx:
            self.prefer(wanted)

        self.assertTrue(TeacherWaitlist.objects.exists())
        self.assertEqual(
            refusal_codes(ctx.exception.considered["preferred_teacher"]),
            ["teacher_double_booked"],
        )

    def test_asking_twice_does_not_stack_entries(self):
        """A client retrying a refused request must not clog the lead's queue."""
        wanted = self.available_teacher(hours=1)
        self.fill_week(wanted, 60)

        for _ in range(3):
            with self.assertRaises(NoCapacity):
                self.prefer(wanted)

        self.assertEqual(TeacherWaitlist.objects.count(), 1)


class HardBlocksDoNotWaitlistTests(PreferredTeacherWorld, TestCase):
    """The spec's exception: some refusals waiting will never fix.

    "not because of a hard block like an unapproved profile or a specialty
    mismatch — those should still fail outright, not waitlist." A queue entry for
    one of these would be a promise the academy cannot keep by waiting.
    """

    def test_a_teacher_who_does_not_teach_the_track_fails_outright(self):
        wanted = self.available_teacher(teaches_level=False)

        with self.assertRaises(ValidationError) as ctx:
            self.prefer(wanted)

        self.assertIn("level", ctx.exception.error_dict)
        self.assertEqual(
            ctx.exception.error_dict["level"][0].code, "teacher_lacks_specialty"
        )
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_an_unapproved_teacher_fails_outright(self):
        wanted = self.available_teacher(approved=False)

        with self.assertRaises(ValidationError) as ctx:
            self.prefer(wanted)

        self.assertIn("teacher", ctx.exception.error_dict)
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_a_past_slot_fails_outright(self):
        """Waiting cannot make yesterday available again."""
        wanted = self.available_teacher()

        with self.assertRaises(ValidationError) as ctx:
            self.prefer(wanted, start_time_utc=dj_timezone.now() - timedelta(days=1))

        self.assertIn("start_time_utc", ctx.exception.error_dict)
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_a_minor_with_no_linked_parent_fails_outright(self):
        """A Phase 1 rule: not a capacity problem, so not a queue.

        The family's own account is incomplete, and joining a waitlist would hide
        that behind "your teacher is busy" — the opposite of what the phase is for.
        """
        wanted = self.available_teacher()

        with self.assertRaises(ValidationError) as ctx:
            self.prefer(wanted, student=MinorStudentFactory())

        self.assertIn("student", ctx.exception.error_dict)
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_a_teacher_who_is_both_full_and_unteaching_fails_outright(self):
        """Every code must be waitlistable, not merely one of them.

        A mixed refusal is a hard block: the capacity half would clear with time,
        the specialty half never would, so the honest answer is the refusal rather
        than a promise resting on the half that could change.
        """
        wanted = self.available_teacher(hours=1, teaches_level=False)
        other_level = LevelFactory(
            track=TrackFactory(
                name="Other",
                slug="other-hb",
                organization=self.level.track.organization,
            )
        )
        teaches(wanted, other_level)
        self.book(
            teacher=wanted,
            level=other_level,
            start_time_utc=self.slot + timedelta(days=1),
            duration_minutes=60,
        )

        with self.assertRaises(ValidationError):
            self.prefer(wanted)

        self.assertFalse(TeacherWaitlist.objects.exists())


class PreferredTeacherIsNeverACohortSeatTests(PreferredTeacherWorld, TestCase):
    """Acceptance criterion 6 — 1:1 by rule, even where a cohort would fit.

    The spec: "A parent naming a specific teacher wants that teacher, not a seat
    in a class — even if that teacher happens to have an open cohort at the right
    time." Logged as a scope decision in tech-debt.md, and pinned here so a later
    phase that wants cohort-aware preferences has to change a test on purpose.
    """

    def setUp(self):
        super().setUp()
        self.level = GroupEligibleLevelFactory()

    def test_a_preferred_request_produces_a_one_to_one_booking(self):
        """Acceptance criterion 6."""
        wanted = self.available_teacher()
        cohort = CohortFactory(
            availability=wanted.availability_windows.first(),
            teacher=wanted,
            level=self.level,
            schedule_start_utc=self.slot,
        )

        routed = self.prefer(wanted)

        self.assertIsNone(routed.booking.cohort, "a preference is never a seat")
        self.assertIsNone(routed.cohort)
        self.assertEqual(routed.reason, RoutedReason.STUDENT_CHOICE)
        self.assertNotEqual(routed.booking.routed_reason, RoutedReason.COHORT_ASSIGNED)

    def test_the_student_is_not_seated_in_the_cohort(self):
        """The other half: no membership either, not merely no ``cohort`` on the row."""
        wanted = self.available_teacher()
        cohort = CohortFactory(
            availability=wanted.availability_windows.first(),
            teacher=wanted,
            level=self.level,
            schedule_start_utc=self.slot,
        )

        self.prefer(wanted)

        self.assertEqual(cohort.seats_taken, 0)
        self.assertNotIn(self.student, cohort.students.all())

    def test_the_same_request_without_a_preference_does_take_the_cohort_seat(self):
        """Proof the rule is the preference and not the world it was tested in.

        Identical setup, no ``preferred_teacher``: Phase 4's cohort-first step
        seats the student. So criterion 6 is about the preference overriding step
        1, not about the cohort being unusable.
        """
        wanted = self.available_teacher()
        cohort = CohortFactory(
            availability=wanted.availability_windows.first(),
            teacher=wanted,
            level=self.level,
            schedule_start_utc=self.slot,
        )

        routed = self.route()

        self.assertEqual(routed.reason, RoutedReason.COHORT_ASSIGNED)
        self.assertEqual(routed.booking.cohort, cohort)
        self.assertEqual(cohort.seats_taken, 1)

    def test_a_one_to_one_preference_clashes_with_the_teachers_own_cohort(self):
        """The overlap rule still applies, and a seat is not the same session.

        Phase 4 exempts seats *sharing* a cohort from the overlap rule; a 1:1
        against a cohort seat is still one teacher in two places. So a teacher
        already teaching their group class at that instant is genuinely busy, and
        the request is waitlisted rather than double-booking them.
        """
        wanted = self.available_teacher()
        cohort = CohortFactory(
            availability=wanted.availability_windows.first(),
            teacher=wanted,
            level=self.level,
            schedule_start_utc=self.slot,
        )
        # An actual seat, so the teacher is committed at that instant.
        self.book(
            teacher=wanted,
            level=self.level,
            cohort=cohort,
            start_time_utc=self.slot,
            duration_minutes=30,
        )

        with self.assertRaises(NoCapacity) as ctx:
            self.prefer(wanted)

        self.assertEqual(
            refusal_codes(ctx.exception.considered["preferred_teacher"]),
            ["teacher_double_booked"],
        )
        self.assertTrue(TeacherWaitlist.objects.exists())


class PromotionTests(PreferredTeacherWorld, TestCase):
    """Acceptance criteria 7 and 8 — the lead grants a request, or cannot."""

    def waiting_entry(self, teacher=None, **overrides):
        """An open entry for a teacher who *could* take it, built via routing.

        Deliberately created the way production creates them — by a refused
        preferred-teacher request — rather than by the factory, so promotion is
        tested against a real entry rather than a plausible one. The teacher is
        then freed, which is the situation a promotion exists for: a slot opened.
        """
        teacher = teacher or self.available_teacher(hours=1)
        self.fill_week(teacher, 60)
        with self.assertRaises(NoCapacity):
            self.prefer(teacher, **overrides)
        entry = TeacherWaitlist.objects.get()
        # The slot opens: cancelling is the one thing that gives capacity back.
        for booking in Booking.objects.filter(teacher=teacher):
            booking.cancel()
        return entry

    def test_promoting_an_entry_creates_the_booking(self):
        """Acceptance criterion 7."""
        entry = self.waiting_entry()

        routed = promote_waitlist_entry(entry, organization=entry.level.track.organization)

        self.assertTrue(routed.booking.pk)
        self.assertEqual(routed.booking.student, entry.student)
        self.assertEqual(routed.booking.teacher, entry.requested_teacher)
        self.assertEqual(routed.booking.level, entry.level)
        self.assertEqual(routed.booking.start_time_utc, entry.requested_start_utc)
        self.assertEqual(routed.booking.routed_reason, RoutedReason.STUDENT_CHOICE)

    def test_promotion_stamps_the_entry_and_keeps_the_row(self):
        """Acceptance criterion 7 — "with the waitlist row still present"."""
        entry = self.waiting_entry()

        routed = promote_waitlist_entry(entry, organization=entry.level.track.organization)

        entry.refresh_from_db()
        self.assertEqual(entry.fulfilled_booking, routed.booking)
        self.assertFalse(entry.is_open)
        self.assertTrue(TeacherWaitlist.objects.filter(pk=entry.pk).exists())

    def test_promotion_goes_through_the_teachers_booking_lock(self):
        """Acceptance criterion 7 — "locked, validated".

        The lock row is the observable trace of ``Booking.save()`` having been
        used: a ``bulk_create`` or a hand-built INSERT would produce the booking
        without it. CLAUDE.md names this as the rule for this phase, and
        test_concurrency.py makes the same check for Phase 4's routing.
        """
        entry = self.waiting_entry()
        teacher = entry.requested_teacher
        TeacherBookingLock.objects.filter(teacher=teacher).delete()

        promote_waitlist_entry(entry, organization=entry.level.track.organization)

        self.assertEqual(
            TeacherBookingLock.objects.filter(teacher=teacher).count(),
            1,
            "promotion must write through Booking.save(), which takes the lock",
        )

    def test_promotion_validates_the_booking(self):
        """The other half of "locked, validated": every ``clean()`` rule ran.

        Proved by its consequences rather than by inspection — the booking has the
        generated video room ``save()`` creates, and it is inside the teacher's
        declared hours, neither of which a raw INSERT would guarantee.
        """
        entry = self.waiting_entry()
        routed = promote_waitlist_entry(entry, organization=entry.level.track.organization)

        self.assertTrue(routed.booking.video_provider_meeting_id)
        self.assertEqual(routed.booking.status, BookingStatus.SCHEDULED)
        # And it really is inside declared hours: re-validating it passes.
        routed.booking.full_clean()

    def test_promotion_can_offer_a_different_slot(self):
        """A lead offering the 10:00 that just opened, not the 09:00 nobody has."""
        entry = self.waiting_entry()
        later = entry.requested_start_utc + timedelta(hours=2)

        routed = promote_waitlist_entry(entry, start_time_utc=later, organization=entry.level.track.organization)

        self.assertEqual(routed.booking.start_time_utc, later)
        entry.refresh_from_db()
        self.assertEqual(
            entry.requested_start_utc,
            later - timedelta(hours=2),
            "the entry keeps what was asked for, not what was given",
        )

    def test_promotion_can_offer_a_different_duration(self):
        entry = self.waiting_entry()
        routed = promote_waitlist_entry(entry, duration_minutes=60, organization=entry.level.track.organization)
        self.assertEqual(routed.booking.duration_minutes, 60)

    def test_promoting_an_ineligible_entry_fails_cleanly(self):
        """Acceptance criterion 8 — capacity filled since the request was made."""
        entry = self.waiting_entry()
        teacher = entry.requested_teacher
        # Somebody else takes the slot in the meantime.
        self.book(
            teacher=teacher,
            level=self.level,
            start_time_utc=entry.requested_start_utc,
            duration_minutes=30,
        )

        with self.assertRaises(ValidationError) as ctx:
            promote_waitlist_entry(entry, organization=entry.level.track.organization)

        self.assertIn("teacher_double_booked", error_codes(ctx.exception))

    def test_a_failed_promotion_leaves_the_entry_open(self):
        """The other half of criterion 8: nothing is forced and nothing is lost."""
        entry = self.waiting_entry()
        self.book(
            teacher=entry.requested_teacher,
            level=self.level,
            start_time_utc=entry.requested_start_utc,
            duration_minutes=30,
        )
        booking_count = Booking.objects.count()

        with self.assertRaises(ValidationError):
            promote_waitlist_entry(entry, organization=entry.level.track.organization)

        entry.refresh_from_db()
        self.assertTrue(entry.is_open, "the family is still waiting")
        self.assertIsNone(entry.fulfilled_booking)
        self.assertEqual(
            Booking.objects.count(), booking_count, "no booking was forced"
        )

    def test_promoting_when_the_teacher_is_full_again_fails_cleanly(self):
        """Criterion 8's own wording — "capacity filled by something else"."""
        entry = self.waiting_entry()
        self.fill_week(entry.requested_teacher, 60)

        with self.assertRaises(ValidationError) as ctx:
            promote_waitlist_entry(entry, organization=entry.level.track.organization)

        self.assertIn("teacher_weekly_capacity_exceeded", error_codes(ctx.exception))
        entry.refresh_from_db()
        self.assertTrue(entry.is_open)

    def test_promoting_when_the_teacher_lost_their_approval_fails_cleanly(self):
        """Drift the entry deliberately survives storing, but not promoting."""
        entry = self.waiting_entry()
        profile = entry.requested_teacher.teacher_profile
        profile.approved = False
        profile.save()

        with self.assertRaises(ValidationError):
            promote_waitlist_entry(entry, organization=entry.level.track.organization)

        entry.refresh_from_db()
        self.assertTrue(entry.is_open)

    def test_promoting_into_a_past_slot_fails_cleanly(self):
        """An entry whose requested time has simply been overtaken by the clock.

        Worth pinning because it is the *ordinary* end of a stale entry, not an
        edge case: nothing expires an entry in this phase (tech-debt.md), so a lead
        working an old queue meets this rather than a missing row.
        """
        entry = self.waiting_entry()

        with self.assertRaises(ValidationError) as ctx:
            promote_waitlist_entry(
                entry, start_time_utc=dj_timezone.now() - timedelta(days=1), organization=entry.level.track.organization
            )

        self.assertIn("start_time_in_past", error_codes(ctx.exception))

    def test_promoting_an_already_fulfilled_entry_is_refused(self):
        """One-way transition, the same shape as reviewing a placement."""
        entry = self.waiting_entry()
        promote_waitlist_entry(entry, organization=entry.level.track.organization)

        with self.assertRaises(WaitlistEntryAlreadyFulfilled):
            promote_waitlist_entry(entry, organization=entry.level.track.organization)

        self.assertEqual(Booking.objects.filter(student=entry.student).count(), 1)

    def test_promotion_never_produces_a_cohort_seat(self):
        """The 1:1 rule holds on this path too, not only on the routing one."""
        self.level = GroupEligibleLevelFactory()
        teacher = self.available_teacher(hours=1)
        entry = self.waiting_entry(teacher=teacher)
        CohortFactory(
            availability=teacher.availability_windows.first(),
            teacher=teacher,
            level=self.level,
            schedule_start_utc=entry.requested_start_utc,
        )

        routed = promote_waitlist_entry(entry, organization=entry.level.track.organization)
        self.assertIsNone(routed.booking.cohort)


class CancellingAPromotedSessionDoesNotReopenTheEntryTests(
    PreferredTeacherWorld, TestCase
):
    """Promotion is one-way, even if the session it produced is cancelled.

    Product owner's call, 2026-08-25: a promoted entry is the permanent record of
    "asked, and was granted a session". A cancellation afterwards is a *new*
    conversation, so the entry stays closed and the family asks again rather than
    silently reappearing in the lead's queue.

    These tests pin that behaviour rather than merely describing it, because the
    alternative — treating a cancelled ``fulfilled_booking`` as still open — is
    equally defensible and would be easy to introduce later by accident. The
    consequence the decision accepts (the family loses their original
    ``requested_at`` and ``priority`` when they re-ask) is spelled out in
    tech-debt.md, so a later phase changes this on purpose.
    """

    def waiting_entry(self, teacher=None, **overrides):
        """An open entry for a teacher who could take it — as ``PromotionTests``."""
        teacher = teacher or self.available_teacher(hours=1)
        self.fill_week(teacher, 60)
        with self.assertRaises(NoCapacity):
            self.prefer(teacher, **overrides)
        entry = TeacherWaitlist.objects.get()
        for booking in Booking.objects.filter(teacher=teacher):
            booking.cancel()
        return entry

    def test_the_entry_stays_closed_after_its_booking_is_cancelled(self):
        entry = self.waiting_entry()
        routed = promote_waitlist_entry(entry, organization=entry.level.track.organization)

        routed.booking.cancel()

        entry.refresh_from_db()
        self.assertFalse(entry.is_open, "promotion is a one-way transition")
        self.assertEqual(entry.fulfilled_booking, routed.booking)

    def test_a_cancelled_promotion_does_not_return_to_the_lead_queue(self):
        """The accepted cost: the family drops out of the queue silently."""
        entry = self.waiting_entry()
        routed = promote_waitlist_entry(entry, organization=entry.level.track.organization)

        routed.booking.cancel()

        self.assertNotIn(
            entry,
            TeacherWaitlist.open_for_teacher(entry.requested_teacher, organization=entry.level.track.organization),
        )

    def test_promoting_again_after_a_cancellation_is_still_refused(self):
        """Not a second bite: the stamp is what closes the entry, not the status."""
        entry = self.waiting_entry()
        routed = promote_waitlist_entry(entry, organization=entry.level.track.organization)
        routed.booking.cancel()

        with self.assertRaises(WaitlistEntryAlreadyFulfilled):
            promote_waitlist_entry(entry, organization=entry.level.track.organization)

    def test_asking_again_creates_a_fresh_entry_at_the_back_of_the_queue(self):
        """A cancellation is a new request, and a new request starts over.

        The partial unique constraint is scoped to *open* entries precisely so
        this is possible — but the new row carries its own ``requested_at``, so
        the family's original wait buys them nothing. That is the tradeoff the
        decision accepts.
        """
        entry = self.waiting_entry()
        teacher = entry.requested_teacher
        routed = promote_waitlist_entry(entry, organization=entry.level.track.organization)
        routed.booking.cancel()
        # The teacher is full again, so re-asking is refused the same way.
        self.fill_week(teacher, 60)

        with self.assertRaises(NoCapacity):
            self.prefer(teacher)

        entries = TeacherWaitlist.objects.filter(
            student=self.student, requested_teacher=teacher
        )
        self.assertEqual(entries.count(), 2, "a second, separate request")
        fresh = entries.get(fulfilled_booking__isnull=True)
        self.assertNotEqual(fresh.pk, entry.pk)
        self.assertGreaterEqual(fresh.requested_at, entry.requested_at)
        self.assertEqual(fresh.priority, 0, "no credit for having waited before")


class PhaseFourRoutingIsUnchangedTests(PreferredTeacherWorld, TestCase):
    """``preferred_teacher`` is additive: omitting it must behave exactly as before.

    Phase 4 has its own suite, but these assert the *seam* — that the new argument
    defaults to the old behaviour rather than subtly re-routing it. A regression
    here would look like a Phase 4 failure with no Phase 4 change to explain it.
    """

    def test_omitting_the_preference_routes_normally(self):
        lead = self.available_teacher(lead=True)
        routed = self.route()

        self.assertEqual(routed.booking.teacher, lead)
        self.assertEqual(routed.reason, RoutedReason.LEAD_AVAILABLE)

    def test_passing_none_is_the_same_as_omitting_it(self):
        lead = self.available_teacher(lead=True)
        routed = route_session(
            student=self.student,
            level=self.level,
            start_time_utc=self.slot,
            duration_minutes=30,
            preferred_teacher=None,
            organization=self.level.track.organization,
        )
        self.assertEqual(routed.booking.teacher, lead)
        self.assertEqual(routed.reason, RoutedReason.LEAD_AVAILABLE)

    def test_the_normal_refusal_still_reports_all_three_steps(self):
        with self.assertRaises(NoCapacity) as ctx:
            self.route()

        self.assertEqual(
            set(ctx.exception.considered), {"cohort", "lead", "sub_teachers"}
        )
        self.assertNotIn("waitlist", ctx.exception.considered)

    def test_the_normal_refusal_creates_no_waitlist_entry(self):
        """Auto-routing has nobody to waitlist *for* — that is the distinction.

        The tempting case, deliberately: a full lead *and* a full sub, so the
        academy genuinely has no capacity and a queue would look helpful. It is
        still wrong here, because the family named nobody — there is no teacher
        whose list they could sensibly be on, and inventing one would be the
        "quietly redirected" behaviour the phase exists to prevent, one step
        removed.
        """
        lead = self.available_teacher(lead=True, hours=1)
        self.fill_week(lead, 60)
        sub = self.available_teacher(hours=1)
        self.fill_week(sub, 60)

        with self.assertRaises(NoCapacity):
            self.route()

        self.assertFalse(TeacherWaitlist.objects.exists())
