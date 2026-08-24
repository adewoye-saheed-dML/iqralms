"""factory_boy factories for the scheduling app.

Per CLAUDE.md every model gets a factory here before tests are written against
it. Two of these derive values rather than hardcoding them, so a factory can
never build a state the model would reject:

* ``BookableTeacherFactory`` attaches an *approved* ``TeacherProfile``, because
  an unapproved teacher cannot hold availability or take a booking.
* ``BookingFactory`` takes an ``availability`` window and derives both the
  teacher and a start time inside that window from it. Overriding ``teacher``
  directly is therefore the wrong lever — pass ``availability=`` instead, or the
  booking lands outside the teacher's declared hours and is rejected.
"""

from datetime import datetime, time, timedelta

import factory
from django.utils import timezone as dj_timezone

from accounts.tests.factories import (
    LeadTeacherFactory,
    StudentFactory,
    SubTeacherFactory,
    TeacherProfileFactory,
)
from curriculum.tests.factories import LevelFactory
from scheduling.models import (
    Availability,
    Booking,
    BookingStatus,
    DEFAULT_DURATION_MINUTES,
    Weekday,
)
from scheduling.utils import UTC, next_date_for_weekday

#: A generous default window, so a test that only cares about "some legal slot"
#: does not have to think about times at all.
DEFAULT_WINDOW_START = time(9, 0)
DEFAULT_WINDOW_END = time(17, 0)


def slot_at(window, offset_minutes: int = 0, on_or_after=None):
    """A UTC datetime ``offset_minutes`` into ``window``'s next occurrence.

    Bookings are datetimes but availability is a weekly rule, so tests need a
    real instant that lands inside a given window. Derived from the window
    rather than hardcoded, so the two cannot drift apart.

    The reference date defaults to *tomorrow*, not today: Phase 3.5 rejects a
    booking whose start is already in the past, and ``next_date_for_weekday``
    returns today when today is the window's weekday — so a window at, say,
    09:00 on this weekday would resolve to 09:00 *today*, which is in the past
    for any test run after 09:00 UTC. Starting the search tomorrow makes every
    derived slot future-dated whatever the wall clock says, while a caller who
    needs a genuinely past slot passes ``on_or_after`` explicitly.
    """
    if on_or_after is None:
        on_or_after = dj_timezone.now().astimezone(UTC).date() + timedelta(days=1)
    date = next_date_for_weekday(window.weekday, on_or_after)
    start = datetime.combine(date, window.start_time_utc, tzinfo=UTC)
    return start + timedelta(minutes=offset_minutes)


class BookableTeacherFactory(SubTeacherFactory):
    """A sub teacher who can actually be booked: approved teacher profile."""

    @factory.post_generation
    def teacher_profile(obj, create, extracted, **kwargs):
        if not create:
            return
        TeacherProfileFactory(user=obj, approved=True, **kwargs)


class UnapprovedTeacherFactory(SubTeacherFactory):
    """A sub teacher whose profile a lead has not approved yet."""

    @factory.post_generation
    def teacher_profile(obj, create, extracted, **kwargs):
        if not create:
            return
        TeacherProfileFactory(user=obj, approved=False, **kwargs)


class BookableLeadTeacherFactory(LeadTeacherFactory):
    """The lead teacher, who is bookable in their own right."""

    @factory.post_generation
    def teacher_profile(obj, create, extracted, **kwargs):
        if not create:
            return
        TeacherProfileFactory(
            user=obj, approved=True, hourly_payout_rate=None, **kwargs
        )


class AvailabilityFactory(factory.django.DjangoModelFactory):
    """One declared window, Monday 09:00-17:00 UTC unless told otherwise."""

    class Meta:
        model = Availability

    teacher = factory.SubFactory(BookableTeacherFactory)
    weekday = Weekday.MONDAY
    start_time_utc = DEFAULT_WINDOW_START
    end_time_utc = DEFAULT_WINDOW_END


class BookingFactory(factory.django.DjangoModelFactory):
    """A scheduled session at the start of its teacher's declared window.

    ``BookingFactory(availability=window)`` books inside ``window``;
    ``BookingFactory(start_time_utc=slot_at(window, 60))`` moves it within the
    window. The teacher always comes from the window.
    """

    class Meta:
        model = Booking
        exclude = ("availability",)

    availability = factory.SubFactory(AvailabilityFactory)
    student = factory.SubFactory(StudentFactory)
    teacher = factory.SelfAttribute("availability.teacher")
    level = factory.SubFactory(LevelFactory)
    start_time_utc = factory.LazyAttribute(lambda o: slot_at(o.availability))
    duration_minutes = DEFAULT_DURATION_MINUTES


class CancelledBookingFactory(BookingFactory):
    """Already cancelled, so it no longer occupies its slot."""

    status = BookingStatus.CANCELLED


class CompletedBookingFactory(BookingFactory):
    """Already taught — attendance history, and no longer cancellable."""

    status = BookingStatus.COMPLETED
