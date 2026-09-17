"""Model-layer tests for ``TeacherWaitlist`` — Phase 5's second half.

The routing behaviour that *creates* entries is in test_waitlist_routing.py and
the HTTP surface is in test_waitlist_api.py. This file is about the row itself:
what it will and will not accept, what stays saveable as the world changes around
it, and the idempotency that stops a retried request stacking duplicates in the
lead's queue.
"""

from datetime import timedelta

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
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from organizations.models import MembershipStatus
from organizations.tests.factories import OrganizationFactory
from scheduling.models import DEFAULT_DURATION_MINUTES, TeacherWaitlist
from scheduling.routing import promote_waitlist_entry

from .factories import (
    AvailabilityFactory,
    BookableTeacherFactory,
    BookingFactory,
    LeadWaitlistEntryFactory,
    WaitlistEntryFactory,
    ensure_teacher_configured,
    slot_at,
    teaches,
)


class WaitlistEntryCreationTests(TestCase):
    """CLAUDE.md's "at least one test per model (creation)"."""

    def test_an_entry_is_created_open(self):
        entry = WaitlistEntryFactory()

        self.assertTrue(entry.pk)
        self.assertTrue(entry.is_open)
        self.assertIsNone(entry.fulfilled_booking)
        self.assertEqual(entry.priority, 0)
        self.assertFalse(entry.notified)
        self.assertIsNotNone(entry.requested_at)

    def test_the_requested_end_is_derived_from_the_start_and_duration(self):
        entry = WaitlistEntryFactory(requested_duration_minutes=45)
        self.assertEqual(
            entry.requested_end_utc, entry.requested_start_utc + timedelta(minutes=45)
        )

    def test_the_duration_defaults_to_the_spec_session_length(self):
        entry = WaitlistEntryFactory()
        self.assertEqual(entry.requested_duration_minutes, DEFAULT_DURATION_MINUTES)

    def test_a_zero_length_request_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            WaitlistEntryFactory(requested_duration_minutes=0)
        self.assertIn("requested_duration_minutes", ctx.exception.error_dict)

    def test_the_lead_teacher_can_be_asked_for_by_name(self):
        """The spec's central case: "I want you specifically"."""
        entry = LeadWaitlistEntryFactory()
        self.assertTrue(entry.requested_teacher.teacher_profile.is_lead)

    def test_the_string_form_names_who_wants_whom_and_the_state(self):
        entry = WaitlistEntryFactory()
        text = str(entry)

        self.assertIn(entry.student.username, text)
        self.assertIn(entry.requested_teacher.username, text)
        self.assertIn("waiting", text)


class WaitlistRoleTests(TestCase):
    """Who may appear on either side of an entry."""

    def test_only_a_student_can_be_waitlisted(self):
        for user in (ParentFactory(), SubTeacherFactory(), LeadTeacherFactory()):
            with self.subTest(role=user.role):
                with self.assertRaises(ValidationError) as ctx:
                    WaitlistEntryFactory(student=user)
                self.assertEqual(
                    ctx.exception.error_dict["student"][0].code,
                    "invalid_student_role",
                )

    def test_only_a_teacher_can_be_asked_for_by_name(self):
        for user in (StudentFactory(), ParentFactory()):
            with self.subTest(role=user.role):
                window = AvailabilityFactory()
                with self.assertRaises(ValidationError) as ctx:
                    TeacherWaitlist.objects.create(
                        student=StudentFactory(),
                        requested_teacher=user,
                        level=LevelFactory(),
                        requested_start_utc=slot_at(window),
                    )
                self.assertEqual(
                    ctx.exception.error_dict["requested_teacher"][0].code,
                    "invalid_teacher_role",
                )

    def test_an_unapproved_teacher_can_still_be_asked_for(self):
        """An entry is a *request*, so it is not gated on bookability.

        Deliberate, and the opposite call from ``Booking.clean()``: waiting for a
        teacher whose approval is pending is a coherent thing for a family to do,
        and *promoting* the entry is where approval is checked. The reverse would
        also freeze existing rows the moment a teacher's approval was revoked —
        the trap learnings.md records for the booking approval gate.
        """
        from .factories import UnapprovedTeacherFactory

        teacher = UnapprovedTeacherFactory()
        level = LevelFactory()
        window = AvailabilityFactory(teacher=BookableTeacherFactory())

        entry = TeacherWaitlist.objects.create(
            student=StudentFactory(),
            requested_teacher=teacher,
            level=level,
            requested_start_utc=slot_at(window),
        )
        self.assertTrue(entry.pk)

    def test_an_entry_stays_saveable_after_its_teacher_loses_approval(self):
        """The frozen-row trap, avoided on purpose.

        If the entry validated bookability, revoking a teacher's approval would
        make every open request for them unsaveable — so nothing could be stamped,
        re-prioritised or otherwise touched. There is a test for exactly this
        failure on the *booking* side (learnings.md, 2026-08-23); this is the same
        shape, handled rather than inherited.
        """
        entry = WaitlistEntryFactory()
        profile = entry.requested_teacher.teacher_profile
        profile.approved = False
        profile.save()

        entry.priority = 5
        entry.save()

        entry.refresh_from_db()
        self.assertEqual(entry.priority, 5)


class WaitlistIdempotencyTests(TestCase):
    """A retried request must not stack duplicates in the lead's queue."""

    def setUp(self):
        self.window = AvailabilityFactory()
        self.teacher = self.window.teacher
        self.student = StudentFactory()
        self.level = LevelFactory()
        teaches(self.teacher, self.level)
        self.slot = slot_at(self.window)

    def record(self, **overrides):
        kwargs = {
            "student": self.student,
            "requested_teacher": self.teacher,
            "level": self.level,
            "requested_start_utc": self.slot,
        }
        kwargs.update(overrides)
        return TeacherWaitlist.record(**kwargs)

    def test_recording_the_same_request_twice_reuses_the_entry(self):
        first = self.record()
        second = self.record()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TeacherWaitlist.objects.count(), 1)

    def test_a_reused_entry_keeps_its_place_in_the_queue(self):
        """Asking again neither costs a family its place nor buys a better one."""
        first = self.record()
        first.priority = 7
        first.save()
        original_requested_at = first.requested_at

        second = self.record()

        self.assertEqual(second.priority, 7)
        self.assertEqual(second.requested_at, original_requested_at)

    def test_a_different_slot_is_a_different_request(self):
        self.record()
        self.record(requested_start_utc=self.slot + timedelta(hours=1))
        self.assertEqual(TeacherWaitlist.objects.count(), 2)

    def test_a_different_teacher_is_a_different_request(self):
        other_window = AvailabilityFactory()
        teaches(other_window.teacher, self.level)

        self.record()
        self.record(requested_teacher=other_window.teacher)
        self.assertEqual(TeacherWaitlist.objects.count(), 2)

    def test_a_different_level_is_a_different_request(self):
        other = LevelFactory(track=TrackFactory(name="Hifz", slug="hifz-w"))
        teaches(self.teacher, other)

        self.record()
        self.record(level=other)
        self.assertEqual(TeacherWaitlist.objects.count(), 2)

    def test_a_fulfilled_request_does_not_block_asking_again(self):
        """The constraint is scoped to *open* entries, so a family can return."""
        first = self.record()
        booking = BookingFactory(
            availability=self.window,
            student=self.student,
            level=self.level,
            start_time_utc=self.slot,
        )
        first.mark_fulfilled(booking)

        second = self.record()

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(TeacherWaitlist.objects.count(), 2)

    def test_a_hand_written_duplicate_is_refused_by_the_database(self):
        """The partial unique constraint, proved to be more than decoration.

        ``record()`` reuses rather than duplicating, so no supported path reaches
        this — which is why the backstop is worth proving. ``bulk_create`` skips
        ``save()`` and is the way in. Wrapped in ``atomic()`` per learnings.md.
        """
        self.record()

        with self.assertRaises(IntegrityError), transaction.atomic():
            TeacherWaitlist.objects.bulk_create(
                [
                    TeacherWaitlist(
                        student=self.student,
                        requested_teacher=self.teacher,
                        level=self.level,
                        requested_start_utc=self.slot,
                    )
                ]
            )


class WaitlistOrderingTests(TestCase):
    """Priority desc, then longest waiting — the order a lead works the list in."""

    def setUp(self):
        self.window = AvailabilityFactory()
        self.teacher = self.window.teacher
        self.level = LevelFactory()
        teaches(self.teacher, self.level)

    def entry(self, *, priority=0, offset_hours=0):
        return WaitlistEntryFactory(
            availability=self.window,
            requested_teacher=self.teacher,
            level=self.level,
            requested_start_utc=slot_at(self.window, offset_hours * 60),
            priority=priority,
        )

    def test_higher_priority_comes_first(self):
        low = self.entry(priority=0, offset_hours=1)
        high = self.entry(priority=10, offset_hours=2)

        self.assertEqual(
            list(TeacherWaitlist.open_for_teacher(self.teacher, organization=self.level.track.organization)), [high, low]
        )

    def test_equal_priority_orders_by_longest_waiting(self):
        """``requested_at`` is auto_now_add, so insertion order is wait order."""
        first = self.entry(offset_hours=1)
        second = self.entry(offset_hours=2)

        self.assertEqual(
            list(TeacherWaitlist.open_for_teacher(self.teacher, organization=self.level.track.organization)), [first, second]
        )

    def test_priority_beats_waiting_time(self):
        """A deliberate bump outranks seniority — that is what the field is for."""
        waited_longest = self.entry(offset_hours=1)
        bumped = self.entry(priority=1, offset_hours=2)

        self.assertEqual(
            list(TeacherWaitlist.open_for_teacher(self.teacher, organization=self.level.track.organization)),
            [bumped, waited_longest],
        )

    def test_a_negative_priority_sorts_last(self):
        """``priority`` is a plain IntegerField, so deprioritising is possible."""
        normal = self.entry(offset_hours=1)
        deprioritised = self.entry(priority=-5, offset_hours=2)

        self.assertEqual(
            list(TeacherWaitlist.open_for_teacher(self.teacher, organization=self.level.track.organization)),
            [normal, deprioritised],
        )

    def test_fulfilled_entries_are_not_in_the_open_queue(self):
        open_entry = self.entry(offset_hours=1)
        done = self.entry(offset_hours=2)
        booking = BookingFactory(
            availability=self.window,
            student=done.student,
            level=self.level,
            start_time_utc=done.requested_start_utc,
        )
        done.mark_fulfilled(booking)

        self.assertEqual(
            list(TeacherWaitlist.open_for_teacher(self.teacher, organization=self.level.track.organization)), [open_entry]
        )

    def test_another_teachers_queue_is_separate(self):
        mine = self.entry(offset_hours=1)
        WaitlistEntryFactory()

        self.assertEqual(
            list(TeacherWaitlist.open_for_teacher(self.teacher, organization=self.level.track.organization)), [mine]
        )

    def test_open_for_teacher_accepts_an_id(self):
        mine = self.entry(offset_hours=1)
        self.assertEqual(
            list(TeacherWaitlist.open_for_teacher(self.teacher.pk, organization=self.level.track.organization)), [mine]
        )


class WaitlistFulfilmentTests(TestCase):
    """``mark_fulfilled`` — the stamp, and what it refuses to stamp."""

    def setUp(self):
        self.entry = WaitlistEntryFactory()
        self.booking = BookingFactory(
            availability=self.entry.requested_teacher.availability_windows.first(),
            student=self.entry.student,
            level=self.entry.level,
            start_time_utc=self.entry.requested_start_utc,
        )

    def test_stamping_a_booking_closes_the_entry_without_deleting_it(self):
        self.entry.mark_fulfilled(self.booking)

        self.entry.refresh_from_db()
        self.assertFalse(self.entry.is_open)
        self.assertEqual(self.entry.fulfilled_booking, self.booking)
        self.assertTrue(TeacherWaitlist.objects.filter(pk=self.entry.pk).exists())

    def test_the_original_request_survives_the_stamp(self):
        """What was wanted stays distinguishable from what was given."""
        wanted = self.entry.requested_start_utc
        self.entry.mark_fulfilled(self.booking)

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.requested_start_utc, wanted)

    def test_a_booking_for_a_different_student_cannot_fulfil_the_entry(self):
        other = BookingFactory(
            availability=self.entry.requested_teacher.availability_windows.first(),
            level=self.entry.level,
            start_time_utc=self.entry.requested_start_utc + timedelta(hours=1),
        )
        with self.assertRaises(ValidationError) as ctx:
            self.entry.mark_fulfilled(other)

        self.assertEqual(
            ctx.exception.error_dict["fulfilled_booking"][0].code,
            "fulfilment_mismatch",
        )

    def test_a_booking_with_a_different_teacher_cannot_fulfil_the_entry(self):
        """The whole point of the phase: somebody else is not a substitute."""
        elsewhere = BookingFactory(
            student=self.entry.student, level=self.entry.level
        )
        with self.assertRaises(ValidationError) as ctx:
            self.entry.mark_fulfilled(elsewhere)

        self.assertIn(
            "requested_teacher",
            ctx.exception.error_dict["fulfilled_booking"][0].message
            % ctx.exception.error_dict["fulfilled_booking"][0].params,
        )

    def test_a_booking_at_a_different_time_may_fulfil_the_entry(self):
        """A lead offering the 10:00 that opened is fulfilling the request.

        The time is deliberately *not* part of the match: an entry records what
        was asked for, and promotion may legitimately offer something else. The
        student, teacher and level must still agree.
        """
        later = BookingFactory(
            availability=self.entry.requested_teacher.availability_windows.first(),
            student=self.entry.student,
            level=self.entry.level,
            start_time_utc=self.entry.requested_start_utc + timedelta(hours=3),
        )
        self.entry.mark_fulfilled(later)

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.fulfilled_booking, later)
        self.assertNotEqual(
            self.entry.fulfilled_booking.start_time_utc, self.entry.requested_start_utc
        )

    def test_a_fulfilled_entrys_booking_cannot_be_deleted_out_from_under_it(self):
        """PROTECT, so a null never silently means "still waiting" again."""
        from django.db.models import ProtectedError

        self.entry.mark_fulfilled(self.booking)
        with self.assertRaises(ProtectedError):
            self.booking.delete()


class PricingDoesNotTouchPriorityTests(TestCase):
    """The phase's open question, answered: premium buys no queue position.

    Product owner's call, 2026-08-25. The spec said not to decide it silently
    either way, so the answer is asserted rather than merely implemented — a later
    phase that changes its mind has to change a test on purpose.
    """

    def test_a_premium_agreement_does_not_raise_priority(self):
        from pricing.tests.factories import PremiumAgreementFactory

        entry = WaitlistEntryFactory()
        PremiumAgreementFactory(student=entry.student, level=entry.level)

        entry.refresh_from_db()
        self.assertEqual(entry.priority, 0)

    def test_an_entry_created_after_a_premium_agreement_starts_at_zero_too(self):
        """Order of operations makes no difference — nothing reads pricing."""
        from pricing.tests.factories import PremiumAgreementFactory

        student = StudentFactory()
        level = LevelFactory()
        PremiumAgreementFactory(student=student, level=level)

        entry = WaitlistEntryFactory(student=student, level=level)
        self.assertEqual(entry.priority, 0)

    def test_priority_is_only_ever_set_by_hand(self):
        entry = WaitlistEntryFactory()
        entry.priority = 10
        entry.save()

        entry.refresh_from_db()
        self.assertEqual(entry.priority, 10, "a manual bump is the only input")


class NotifiedIsForALaterPhaseTests(TestCase):
    """``notified`` exists per the spec's field list; nothing in this phase sets it.

    Automatic notification is explicitly out of scope (spec, tech-debt.md), so the
    field's default is the whole of its behaviour right now. Asserted so that
    "nothing notifies anybody yet" is a recorded fact rather than an assumption.
    """

    def test_an_entry_starts_un_notified(self):
        self.assertFalse(WaitlistEntryFactory().notified)

    def test_promotion_does_not_set_notified(self):
        entry = WaitlistEntryFactory()
        promote_waitlist_entry(entry, organization=entry.level.track.organization)

        entry.refresh_from_db()
        self.assertFalse(
            entry.notified,
            "nothing in this phase delivers a notification, so nothing claims to",
        )


class WaitlistTenancyTests(TestCase):
    """SaaS Phase 4 Task 4.8: Waitlist ownership, promotion, and isolation."""

    def setUp(self):
        self.org_a = OrganizationFactory(name="Academy A")
        self.org_b = OrganizationFactory(name="Academy B")
        self.track_a = TrackFactory(organization=self.org_a)
        self.track_b = TrackFactory(organization=self.org_b)
        self.level_a = LevelFactory(track=self.track_a)
        self.level_b = LevelFactory(track=self.track_b)
        self.student_a = StudentFactory()
        admit(self.student_a, self.org_a)
        self.student_b = StudentFactory()
        admit(self.student_b, self.org_b)

        self.teacher_a = BookableTeacherFactory()
        ensure_teacher_configured(self.teacher_a, self.org_a)
        teaches(self.teacher_a, self.level_a)
        self.avail_a = AvailabilityFactory(
            teacher=self.teacher_a,
            organization=self.org_a,
        )

        self.teacher_b = BookableTeacherFactory()
        ensure_teacher_configured(self.teacher_b, self.org_b)
        teaches(self.teacher_b, self.level_b)
        self.avail_b = AvailabilityFactory(
            teacher=self.teacher_b,
            organization=self.org_b,
        )

    def test_waitlist_ownership_derived_from_level_track_organization(self):
        entry_a = WaitlistEntryFactory(
            availability=self.avail_a,
            level=self.level_a,
            student=self.student_a,
        )
        self.assertEqual(entry_a.organization, self.org_a)

    def test_queryset_in_organization_scopes_entries(self):
        entry_a = WaitlistEntryFactory(
            availability=self.avail_a,
            level=self.level_a,
            student=self.student_a,
        )
        entry_b = WaitlistEntryFactory(
            availability=self.avail_b,
            level=self.level_b,
            student=self.student_b,
        )

        a_entries = list(TeacherWaitlist.objects.in_organization(self.org_a))
        b_entries = list(TeacherWaitlist.objects.in_organization(self.org_b))

        self.assertIn(entry_a, a_entries)
        self.assertNotIn(entry_b, a_entries)
        self.assertIn(entry_b, b_entries)
        self.assertNotIn(entry_a, b_entries)

    def test_open_for_teacher_filters_by_organization(self):
        # Teacher configured in both orgs
        ensure_teacher_configured(self.teacher_a, self.org_b)
        teaches(self.teacher_a, self.level_b)
        avail_a_in_b = AvailabilityFactory(
            teacher=self.teacher_a,
            organization=self.org_b,
        )

        entry_a = WaitlistEntryFactory(
            availability=self.avail_a,
            level=self.level_a,
            student=self.student_a,
            requested_teacher=self.teacher_a,
        )
        entry_b = WaitlistEntryFactory(
            availability=avail_a_in_b,
            level=self.level_b,
            student=self.student_b,
            requested_teacher=self.teacher_a,
        )

        a_open = list(TeacherWaitlist.open_for_teacher(self.teacher_a, organization=self.org_a))
        b_open = list(TeacherWaitlist.open_for_teacher(self.teacher_a, organization=self.org_b))

        self.assertIn(entry_a, a_open)
        self.assertNotIn(entry_b, a_open)
        self.assertIn(entry_b, b_open)
        self.assertNotIn(entry_a, b_open)

    def test_promote_waitlist_entry_with_mismatched_organization_rejected(self):
        entry_a = WaitlistEntryFactory(
            availability=self.avail_a,
            level=self.level_a,
            student=self.student_a,
        )
        with self.assertRaises(ValidationError) as ctx:
            promote_waitlist_entry(entry_a, organization=self.org_b)
        self.assertIn("entry", ctx.exception.error_dict)

    def test_promote_waitlist_entry_enforces_active_student_membership(self):
        entry_a = WaitlistEntryFactory(
            availability=self.avail_a,
            level=self.level_a,
            student=self.student_a,
        )
        # Deactivate student membership in org_a
        self.student_a.organization_memberships.filter(organization=self.org_a).update(
            status=MembershipStatus.SUSPENDED
        )
        with self.assertRaises(ValidationError) as ctx:
            promote_waitlist_entry(entry_a, organization=entry_a.level.track.organization)
        self.assertIn("student", ctx.exception.error_dict)
        self.assertEqual(
            ctx.exception.error_dict["student"][0].code,
            "student_not_active_member",
        )

    def test_promote_waitlist_entry_enforces_teacher_approval(self):
        entry_a = WaitlistEntryFactory(
            availability=self.avail_a,
            level=self.level_a,
            student=self.student_a,
        )
        from accounts.models import OrganizationTeacherConfiguration

        OrganizationTeacherConfiguration.objects.filter(
            membership__user=self.teacher_a,
            membership__organization=self.org_a,
        ).update(approved=False)

        with self.assertRaises(ValidationError) as ctx:
            promote_waitlist_entry(entry_a, organization=entry_a.level.track.organization)
        self.assertIn("teacher", ctx.exception.error_dict)
        self.assertEqual(
            ctx.exception.error_dict["teacher"][0].code,
            "teacher_not_approved",
        )

    def test_promote_waitlist_entry_enforces_active_teachertrack(self):
        entry_a = WaitlistEntryFactory(
            availability=self.avail_a,
            level=self.level_a,
            student=self.student_a,
        )
        from curriculum.models import TeacherTrack

        TeacherTrack.objects.filter(
            membership__user=self.teacher_a,
            track=self.track_a,
        ).update(active=False)

        with self.assertRaises(ValidationError) as ctx:
            promote_waitlist_entry(entry_a, organization=entry_a.level.track.organization)
        self.assertIn("level", ctx.exception.error_dict)
        self.assertEqual(
            ctx.exception.error_dict["level"][0].code,
            "teacher_lacks_specialty",
        )

