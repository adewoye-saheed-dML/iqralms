"""factory_boy factories for the scheduling app.

Per CLAUDE.md every model gets a factory here before tests are written against
it. Several of these derive values rather than hardcoding them, so a factory can
never build a state the model would reject:

* ``BookableTeacherFactory`` attaches an *approved* ``TeacherProfile``, because
  an unapproved teacher cannot hold availability or take a booking.
* ``BookingFactory`` takes an ``availability`` window and derives both the
  teacher and a start time inside that window from it. Overriding ``teacher``
  directly is therefore the wrong lever — pass ``availability=`` instead, or the
  booking lands outside the teacher's declared hours and is rejected.
* ``BookingFactory`` and ``CohortFactory`` also record the level's track among
  the teacher's specialties before saving (Phase 4 makes that a hard rule). A
  test that wants the *rejection* builds its booking directly rather than through
  the factory — see ``teaches`` below.
* ``WaitlistEntryFactory`` (Phase 5) derives its requested slot from a window the
  same way, so an entry built by the factory is one a promotion could actually
  fulfil. A test that wants promotion to *fail* fills the teacher's week or
  narrows their hours after building the entry, which is exactly the drift the
  entry exists to survive.
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
from curriculum.tests.factories import GroupEligibleLevelFactory, LevelFactory, admit
from organizations.tests.factories import OrganizationFactory
from scheduling.models import (
    Availability,
    Booking,
    BookingStatus,
    Cohort,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_MAX_STUDENTS,
    TeacherWaitlist,
    Weekday,
)
from scheduling.utils import UTC, next_date_for_weekday

#: A generous default window, so a test that only cares about "some legal slot"
#: does not have to think about times at all.
DEFAULT_WEEKDAY = Weekday.MONDAY
DEFAULT_WINDOW_START = time(9, 0)
DEFAULT_WINDOW_END = time(17, 0)


def teaches(teacher, level):
    """Record ``level.track`` among ``teacher``'s specialties, and return them.

    Phase B07: The authoritative assignment is now TeacherTrack (academy-scoped).
    The legacy ``TeacherProfile.specialties`` M2M is no longer written to.
    Any test that builds a booking without going through ``BookingFactory``
    needs this first, and any test asserting the rejection needs to *not* call it.
    """
    if (
        hasattr(level, "track")
        and hasattr(level.track, "organization")
        and level.track.organization is not None
    ):
        org = level.track.organization
        ensure_teacher_configured(teacher, org)
        from curriculum.models import TeacherTrack

        memberships = getattr(teacher, "organization_memberships", None)
        if memberships is not None:
            m = memberships.filter(organization=org).first()
            if m:
                TeacherTrack.objects.update_or_create(
                    membership=m,
                    track=level.track,
                    defaults={"active": True},
                )
    return teacher


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


def ensure_teacher_configured(teacher, organization):
    """Ensure teacher has active membership and OrganizationTeacherConfiguration."""
    if teacher is None or organization is None:
        return
    if not getattr(teacher, "is_teacher", False):
        return
    admit(teacher, organization)
    from accounts.models import OrganizationTeacherConfiguration

    memberships = getattr(teacher, "organization_memberships", None)
    if memberships is not None:
        m = memberships.filter(organization=organization).first()
        if (
            m
            and not OrganizationTeacherConfiguration.objects.filter(
                membership=m
            ).exists()
        ):
            profile = getattr(teacher, "teacher_profile", None)
            approved = getattr(profile, "approved", True)
            max_hours = getattr(profile, "max_weekly_hours", 20) or 20
            payout_rate = getattr(profile, "hourly_payout_rate", None)
            OrganizationTeacherConfiguration.objects.create(
                membership=m,
                approved=approved,
                max_weekly_hours=max_hours,
                hourly_payout_rate=payout_rate,
            )


class AvailabilityFactory(factory.django.DjangoModelFactory):
    """One declared window, Monday 09:00-17:00 UTC unless told otherwise."""

    class Meta:
        model = Availability

    organization = factory.SubFactory(OrganizationFactory)
    teacher = factory.SubFactory(BookableTeacherFactory)
    weekday = DEFAULT_WEEKDAY
    start_time_utc = DEFAULT_WINDOW_START
    end_time_utc = DEFAULT_WINDOW_END

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        teacher = kwargs.get("teacher")
        organization = kwargs.get("organization")
        if teacher is not None and organization is not None:
            ensure_teacher_configured(teacher, organization)
        return super()._create(model_class, *args, **kwargs)


class BookingFactory(factory.django.DjangoModelFactory):
    """A scheduled session at the start of its teacher's declared window.

    ``BookingFactory(availability=window)`` books inside ``window``;
    ``BookingFactory(start_time_utc=slot_at(window, 60))`` moves it within the
    window. The teacher always comes from the window.
    """

    class Meta:
        model = Booking

    availability = factory.SubFactory(AvailabilityFactory)
    student = factory.SubFactory(StudentFactory)
    teacher = factory.SelfAttribute("availability.teacher")
    level = None
    start_time_utc = factory.LazyAttribute(lambda o: slot_at(o.availability))
    duration_minutes = DEFAULT_DURATION_MINUTES

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Grant the specialty first, then build — a factory keeps its promise.

        Phase 4 rejects a booking whose teacher does not teach the level's track,
        and both defaults here are independent (a window's teacher, a fresh
        level's fresh track), so without this the factory would build nothing but
        rejections. It cannot be a ``post_generation`` hook: those run *after*
        ``save()``, which is where the rule fires.

        A test that wants the rejection constructs its ``Booking`` directly and
        simply does not call ``teaches``.
        """
        availability = kwargs.pop("availability", None)
        level = kwargs.get("level")
        teacher = kwargs.get("teacher")

        if teacher is not None and availability is not None and availability.teacher != teacher:
            existing = teacher.availability_windows.first()
            if existing is not None:
                availability = existing
                if "start_time_utc" not in kwargs:
                    kwargs["start_time_utc"] = slot_at(existing)
            else:
                availability.teacher = teacher
                ensure_teacher_configured(teacher, availability.organization)
                availability.save()

        if availability is not None:
            if level is None:
                level = LevelFactory(track__organization=availability.organization)
                kwargs["level"] = level
            else:
                if (
                    hasattr(level, "track")
                    and hasattr(level.track, "organization")
                    and availability.organization_id != level.track.organization_id
                ):
                    ensure_teacher_configured(
                        availability.teacher, level.track.organization
                    )
                    Availability.objects.filter(pk=availability.pk).update(
                        organization=level.track.organization
                    )
                    availability.organization = level.track.organization

            ensure_teacher_configured(
                availability.teacher, availability.organization
            )

        if teacher is not None and level is not None:
            teaches(teacher, level)
            if hasattr(level, "track") and hasattr(level.track, "organization"):
                ensure_teacher_configured(teacher, level.track.organization)

        student = kwargs.get("student")
        if (
            student is not None
            and level is not None
            and hasattr(level, "track")
            and hasattr(level.track, "organization")
            and level.track.organization is not None
        ):
            from curriculum.tests.factories import admit

            admit(student, level.track.organization)

        return super()._create(model_class, *args, **kwargs)


class CancelledBookingFactory(BookingFactory):
    """Already cancelled, so it no longer occupies its slot."""

    status = BookingStatus.CANCELLED


class CompletedBookingFactory(BookingFactory):
    """Already taught — attendance history, and no longer cancellable."""

    status = BookingStatus.COMPLETED


class CohortFactory(factory.django.DjangoModelFactory):
    """An open group class starting inside its teacher's declared hours.

    Same shape as ``BookingFactory``: pass ``availability=`` to control the
    teacher and the window, and the start time is derived from it so a seat
    booking is actually creatable. The level is group-eligible by default,
    because a cohort on any other kind of level is a state ``Cohort.clean()``
    refuses.
    """

    class Meta:
        model = Cohort

    availability = factory.SubFactory(AvailabilityFactory)
    teacher = factory.SelfAttribute("availability.teacher")
    level = None
    max_students = DEFAULT_MAX_STUDENTS
    schedule_start_utc = factory.LazyAttribute(lambda o: slot_at(o.availability))

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        # Cohort.clean() refuses a teacher who does not teach the level's track,
        # for the same reason Booking.clean() does — see BookingFactory._create.
        availability = kwargs.pop("availability", None)
        level = kwargs.get("level")
        teacher = kwargs.get("teacher")

        if teacher is not None and availability is not None and availability.teacher != teacher:
            existing = teacher.availability_windows.first()
            if existing is not None:
                availability = existing
                if "schedule_start_utc" not in kwargs:
                    kwargs["schedule_start_utc"] = slot_at(existing)
            else:
                availability.teacher = teacher
                ensure_teacher_configured(teacher, availability.organization)
                availability.save()

        if availability is not None:
            if level is None:
                level = GroupEligibleLevelFactory(
                    track__organization=availability.organization
                )
                kwargs["level"] = level
            else:
                if (
                    hasattr(level, "track")
                    and hasattr(level.track, "organization")
                    and availability.organization_id != level.track.organization_id
                ):
                    ensure_teacher_configured(
                        availability.teacher, level.track.organization
                    )
                    Availability.objects.filter(pk=availability.pk).update(
                        organization=level.track.organization
                    )
                    availability.organization = level.track.organization

            ensure_teacher_configured(
                availability.teacher, availability.organization
            )

        if teacher is not None and level is not None:
            teaches(teacher, level)
            if hasattr(level, "track") and hasattr(level.track, "organization"):
                ensure_teacher_configured(teacher, level.track.organization)

        return super()._create(model_class, *args, **kwargs)


class LeadCohortFactory(CohortFactory):
    """A group class the lead teacher runs themselves."""

    availability = factory.SubFactory(
        AvailabilityFactory, teacher=factory.SubFactory(BookableLeadTeacherFactory)
    )


class WaitlistEntryFactory(factory.django.DjangoModelFactory):
    """An open request for one teacher by name, for a slot they could take.

    Same shape as ``BookingFactory``: pass ``availability=`` to control the
    teacher and the window, and the requested slot is derived from it — so an
    entry built here is one ``promote_waitlist_entry`` can actually fulfil, which
    is what makes the *failure* tests meaningful (they take the capacity away
    afterwards rather than never having had it).

    The specialty is granted for the same reason ``BookingFactory`` grants it: a
    promotion runs the full ``Booking.clean()``, so an entry naming a teacher who
    does not teach the level could only ever fail.
    """

    class Meta:
        model = TeacherWaitlist

    availability = factory.SubFactory(AvailabilityFactory)
    student = factory.SubFactory(StudentFactory)
    requested_teacher = factory.SelfAttribute("availability.teacher")
    level = None
    requested_start_utc = factory.LazyAttribute(lambda o: slot_at(o.availability))
    requested_duration_minutes = DEFAULT_DURATION_MINUTES

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        # TeacherWaitlist.clean() does not check the specialty — an entry is a
        # request, not a session — but promoting one does, so a factory-built
        # entry that could never be promoted would be a trap. Granted here for
        # the same reason BookingFactory does it, and in _create rather than a
        # post_generation hook so it is in place before save().
        availability = kwargs.pop("availability", None)
        level = kwargs.get("level")
        teacher = kwargs.get("requested_teacher")

        if availability is not None:
            if level is None:
                level = LevelFactory(track__organization=availability.organization)
                kwargs["level"] = level
            else:
                if (
                    hasattr(level, "track")
                    and hasattr(level.track, "organization")
                    and availability.organization_id != level.track.organization_id
                ):
                    ensure_teacher_configured(
                        availability.teacher, level.track.organization
                    )
                    Availability.objects.filter(pk=availability.pk).update(
                        organization=level.track.organization
                    )
                    availability.organization = level.track.organization

            ensure_teacher_configured(
                availability.teacher, availability.organization
            )

        if teacher is not None and level is not None:
            teaches(teacher, level)
            if hasattr(level, "track") and hasattr(level.track, "organization"):
                ensure_teacher_configured(teacher, level.track.organization)

        student = kwargs.get("student")
        if (
            student is not None
            and level is not None
            and hasattr(level, "track")
            and hasattr(level.track, "organization")
            and level.track.organization is not None
        ):
            from curriculum.tests.factories import admit

            admit(student, level.track.organization)

        return super()._create(model_class, *args, **kwargs)


class LeadWaitlistEntryFactory(WaitlistEntryFactory):
    """A family asking for the lead teacher by name — the spec's central case."""

    availability = factory.SubFactory(
        AvailabilityFactory, teacher=factory.SubFactory(BookableLeadTeacherFactory)
    )
