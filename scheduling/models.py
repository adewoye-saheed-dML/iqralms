"""Scheduling: when a teacher is free, and who is booked when.

Field sets mirror specs/phase-3-scheduling.md exactly. What is deliberately
*not* here, per that spec: cohorts, capacity limits (``max_weekly_hours`` is not
enforced yet), routing, pricing. No routing-related column exists on ``Booking``
— Phase 4 adds what it needs when it needs it.

Three decisions worth knowing before reading:

* Validation lives in ``clean()``, called from ``save()``, the same pattern as
  Phase 1's role checks. The spec asks for the overlap rule specifically not to
  be an API-layer-only check, and neither rule can be a DB constraint: one
  reads a different table (``Availability``), the other compares ranges across
  sibling rows.
* The availability check runs on *creation* only, while the overlap check runs
  for as long as a booking is ``scheduled``. Otherwise a teacher editing their
  hours would make an existing booking unsaveable — including uncancellable,
  since ``cancel()`` goes through ``save()``. A cancelled booking frees its
  slot, which is what makes cancel-and-rebook the supported reschedule path.
* Times are UTC end to end (CLAUDE.md). Conversion to a person's own zone
  happens in serializers, using ``utils.utc_time_to_local``.
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from accounts.models import Role, User
from curriculum.models import Level

from .exceptions import BookingNotCancellable
from .utils import local_window_to_utc, split_utc_interval, utc_time_to_local

#: Spec default session length.
DEFAULT_DURATION_MINUTES = 30

#: Prefixed so a room is identifiable in a shared Jitsi namespace; the uuid is
#: what makes it unguessable.
VIDEO_ROOM_PREFIX = "quranacademy-"

#: An upper bound on any stored booking's length, used to bound the overlap
#: query. It holds by construction: a booking must fit inside one availability
#: window, and a window cannot span more than a single UTC day.
LONGEST_POSSIBLE_BOOKING = timedelta(days=1)


class Weekday(models.IntegerChoices):
    """Monday=0 .. Sunday=6, matching ``datetime.date.weekday()``."""

    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class BookingStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    COMPLETED = "completed", "Completed"
    NO_SHOW = "no_show", "No show"
    CANCELLED = "cancelled", "Cancelled"


def generate_video_room_name() -> str:
    """An unguessable Jitsi room identifier.

    No API call creates the room — it exists the moment someone joins it, so
    uniqueness and unguessability are the only requirements.
    """
    return f"{VIDEO_ROOM_PREFIX}{uuid.uuid4().hex}"


def bookable_teacher_error(user):
    """Why ``user`` cannot be booked or hold availability, or None if they can.

    Returns the ``ValidationError`` rather than raising it, so callers can
    attach it to whichever field is theirs.
    """
    if not user.is_teacher:
        return ValidationError(
            "Only a lead or sub teacher can teach a session (got '%(role)s').",
            code="invalid_teacher_role",
            params={"role": user.role},
        )
    # RelatedObjectDoesNotExist subclasses AttributeError, so getattr's default
    # covers "teacher has no profile at all".
    profile = getattr(user, "teacher_profile", None)
    if profile is None:
        return ValidationError(
            "%(username)s has no teacher profile, so they are not bookable.",
            code="no_teacher_profile",
            params={"username": user.username},
        )
    if not profile.approved:
        return ValidationError(
            "%(username)s's teacher profile is not approved yet.",
            code="teacher_not_approved",
            params={"username": user.username},
        )
    return None


class Availability(models.Model):
    """One weekly window a teacher declares themselves free in, stored UTC.

    A teacher may hold any number of these — several windows in a day, or
    different windows on different days. A window may not wrap past midnight
    UTC; ``create_from_local`` splits a local window that does into the two
    rows that represent it.
    """

    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="availability_windows",
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time_utc = models.TimeField(
        help_text="UTC. The teacher states local time; entry converts it."
    )
    end_time_utc = models.TimeField(help_text="UTC, exclusive. Must be after the start.")

    class Meta:
        ordering = ["teacher", "weekday", "start_time_utc"]
        verbose_name_plural = "availabilities"

    # --- Behaviour ----------------------------------------------------------

    @classmethod
    def create_from_local(
        cls, *, teacher, weekday, start_local, end_local, tz_name=None, on_or_after=None
    ):
        """Create the row(s) for a window a teacher stated in their own zone.

        This is the point-of-entry conversion CLAUDE.md asks for: local time is
        never stored. Returns a list, because converting can split one local
        window across two UTC days (and shift its weekday) — see utils.py.
        """
        tz_name = tz_name or teacher.timezone
        return [
            cls.objects.create(
                teacher=teacher,
                weekday=utc_weekday,
                start_time_utc=start_utc,
                end_time_utc=end_utc,
            )
            for utc_weekday, start_utc, end_utc in local_window_to_utc(
                weekday, start_local, end_local, tz_name, on_or_after
            )
        ]

    def covers(self, weekday: int, start_time, end_time) -> bool:
        """Whether ``[start_time, end_time)`` on ``weekday`` sits inside this window."""
        return (
            self.weekday == weekday
            and self.start_time_utc <= start_time
            and end_time <= self.end_time_utc
        )

    def local_window(self, tz_name):
        """This window as ``(weekday, start, end)`` in ``tz_name``, for display."""
        weekday, start = utc_time_to_local(self.weekday, self.start_time_utc, tz_name)
        _, end = utc_time_to_local(self.weekday, self.end_time_utc, tz_name)
        return (weekday, start, end)

    # --- Validation ---------------------------------------------------------

    def clean(self):
        errors = {}

        if self.teacher_id:
            teacher_error = bookable_teacher_error(self.teacher)
            if teacher_error is not None:
                errors["teacher"] = teacher_error

        if self.start_time_utc and self.end_time_utc:
            if self.end_time_utc <= self.start_time_utc:
                errors["end_time_utc"] = ValidationError(
                    "A window must end after it starts. A window that runs past "
                    "midnight UTC is stored as two rows, one per UTC day.",
                    code="window_not_forward",
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # The approved-profile rule reads another table, so it cannot be a DB
        # constraint; validating here makes it hold for the admin and direct ORM
        # writes as well as the API.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.teacher.username} {self.get_weekday_display()} "
            f"{self.start_time_utc:%H:%M}-{self.end_time_utc:%H:%M} UTC"
        )


class Booking(models.Model):
    """One 1:1 session between a student and a teacher, at a UTC instant.

    Rescheduling is deliberately not supported this phase: cancel and recreate.
    That keeps ``video_room_name`` immutable, which the spec requires.
    """

    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="bookings",
        help_text="Role 'student'. A minor needs a linked parent first.",
    )
    teacher = models.ForeignKey(
        # PROTECT, not CASCADE: completed sessions are what a sub-teacher's
        # payout is computed from, so deleting a teacher must be a deliberate
        # act rather than a cascade (product owner's call, 2026-08-23).
        User,
        on_delete=models.PROTECT,
        related_name="teaching_bookings",
        help_text="Role 'lead' or 'sub', with an approved teacher profile.",
    )
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="bookings")
    start_time_utc = models.DateTimeField(help_text="Stored UTC, always.")
    duration_minutes = models.PositiveIntegerField(
        default=DEFAULT_DURATION_MINUTES,
        validators=[MinValueValidator(1)],
    )
    status = models.CharField(
        max_length=16,
        choices=BookingStatus.choices,
        default=BookingStatus.SCHEDULED,
    )
    video_room_name = models.CharField(
        max_length=64,
        unique=True,
        blank=True,
        help_text=(
            "Jitsi room identifier, generated once at creation and never "
            "changed — not even by a reschedule."
        ),
    )

    class Meta:
        ordering = ["start_time_utc", "pk"]

    # --- Behaviour ----------------------------------------------------------

    @property
    def end_time_utc(self):
        """Exclusive end of the session. Derived, never stored."""
        if self.start_time_utc is None or self.duration_minutes is None:
            return None
        return self.start_time_utc + timedelta(minutes=self.duration_minutes)

    @property
    def video_join_url(self) -> str:
        """The join link both sides use. The room exists once someone joins it."""
        return f"https://{settings.JITSI_DOMAIN}/{self.video_room_name}"

    def cancel(self):
        """Set ``status=cancelled``. The row stays — history for payouts later."""
        if self.status != BookingStatus.SCHEDULED:
            raise BookingNotCancellable(
                f"A booking that is '{self.get_status_display().lower()}' cannot "
                "be cancelled."
            )
        self.status = BookingStatus.CANCELLED
        self.save()
        return self

    def utc_segments(self):
        """This session split per UTC day: ``[(weekday, start, end)]``.

        One entry for any bookable session; more than one means it runs across
        midnight UTC, which no single availability window can cover.
        """
        if self.start_time_utc is None or self.duration_minutes is None:
            return []
        return list(split_utc_interval(self.start_time_utc, self.end_time_utc))

    def clashing_bookings(self):
        """Scheduled bookings for this teacher whose time range overlaps ours.

        The range comparison happens in Python because ``duration_minutes`` is
        an integer, not an interval — expressing ``start + duration`` in SQL is
        backend-specific, and this project is still on SQLite (tech-debt.md).
        The query bound keeps that from meaning a full table scan, and is exact:
        no stored booking can be longer than LONGEST_POSSIBLE_BOOKING.
        """
        if not self.teacher_id or self.start_time_utc is None:
            return []
        candidates = type(self).objects.filter(
            teacher_id=self.teacher_id,
            status=BookingStatus.SCHEDULED,
            start_time_utc__lt=self.end_time_utc,
            start_time_utc__gt=self.start_time_utc - LONGEST_POSSIBLE_BOOKING,
        )
        if self.pk:
            candidates = candidates.exclude(pk=self.pk)
        return [
            other
            for other in candidates.only("start_time_utc", "duration_minutes")
            # Half-open ranges: a session starting exactly when another ends is
            # not a clash.
            if other.end_time_utc > self.start_time_utc
        ]

    # --- Validation ---------------------------------------------------------

    def _validate_student(self, errors):
        if self.student.role != Role.STUDENT:
            errors["student"] = ValidationError(
                "Only a user with role 'student' can be booked into a session "
                "(got '%(role)s').",
                code="invalid_student_role",
                params={"role": self.student.role},
            )
        elif not self.student.is_fully_active:
            # is_fully_active, not is_active — the Phase 1 distinction that
            # exists for exactly this gate (see learnings.md).
            errors["student"] = ValidationError(
                "A minor student cannot be booked until a parent is linked to "
                "their account.",
                code="student_not_fully_active",
            )

    def _validate_within_availability(self, errors):
        segments = self.utc_segments()
        if not segments:
            return
        if len(segments) > 1:
            errors[NON_FIELD_ERRORS] = ValidationError(
                "A session cannot run across midnight UTC, because a single "
                "availability window never does. Book the two halves "
                "separately.",
                code="crosses_utc_midnight",
            )
            return

        weekday, start_time, end_time = segments[0]
        windows = self.teacher.availability_windows.filter(weekday=weekday)
        if not any(window.covers(weekday, start_time, end_time) for window in windows):
            errors[NON_FIELD_ERRORS] = ValidationError(
                "%(teacher)s has no availability covering %(day)s "
                "%(start)s-%(end)s UTC.",
                code="outside_availability",
                params={
                    "teacher": self.teacher.username,
                    "day": Weekday(weekday).label,
                    "start": start_time.strftime("%H:%M"),
                    "end": end_time.strftime("%H:%M"),
                },
            )

    def clean(self):
        errors = {}

        if self.student_id:
            self._validate_student(errors)

        teacher_ok = False
        if self.teacher_id:
            teacher_error = bookable_teacher_error(self.teacher)
            if teacher_error is None:
                teacher_ok = True
            else:
                errors["teacher"] = teacher_error

        if not self._state.adding and self.video_room_name:
            stored = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("video_room_name", flat=True)
                .first()
            )
            if stored and self.video_room_name != stored:
                errors["video_room_name"] = ValidationError(
                    "A booking's video room never changes. Cancel and recreate "
                    "instead of rescheduling.",
                    code="video_room_immutable",
                )

        timed = self.start_time_utc is not None and self.duration_minutes
        if timed and teacher_ok and self.status == BookingStatus.SCHEDULED:
            # Creation only: a teacher later editing their hours must not make
            # an existing booking unsaveable, cancellation included.
            if self._state.adding:
                self._validate_within_availability(errors)

            if self.clashing_bookings():
                errors.setdefault(
                    NON_FIELD_ERRORS,
                    ValidationError(
                        "%(teacher)s already has a scheduled session overlapping "
                        "that time.",
                        code="teacher_double_booked",
                        params={"teacher": self.teacher.username},
                    ),
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.video_room_name:
            # Generated once, before the first validation, so the uniqueness
            # check in full_clean() covers it too.
            self.video_room_name = generate_video_room_name()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.student.username} with {self.teacher.username} "
            f"{self.start_time_utc:%Y-%m-%d %H:%M} UTC ({self.status})"
        )
