"""Scheduling: when a teacher is free, who is booked when, and who teaches it.

Field sets mirror specs/phase-3-scheduling.md, specs/phase-4-routing.md and
specs/phase-5-pricing-waitlist.md exactly. What is deliberately *not* here:
pricing (that is the ``pricing`` app — a rate is a lookup, not a schedule) and
rubric-based ranking, which Phase 4's spec names as out of scope and warns
specifically against letting step 3's "most remaining capacity" rule grow into.

Decisions worth knowing before reading:

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
  The past-start rule added in Phase 3.5 has that same scope, and for the same
  reason: a session becomes past-dated simply by being taught.
* Times are UTC end to end (CLAUDE.md). Conversion to a person's own zone
  happens in serializers, using ``utils.utc_time_to_local``.
* Creating a booking holds ``TeacherBookingLock`` for that teacher (Phase 3.5).
  The overlap rule is a read followed by a write, so without it two concurrent
  requests for one slot both pass a clean check and both commit. Phase 4's
  routing and Phase 5's waitlist promotion both write bookings through
  ``Booking.save()`` for exactly this reason — a ``bulk_create`` of cohort seats
  or promoted entries would bypass the lock and ``clean()`` alike.
* Phase 4 adds two creation-time rules that apply to *every* booking, routed or
  directly booked: the teacher must specialise in the level's track, and the
  booking must not push them past ``max_weekly_hours``. Both are creation-only,
  for the same reason the availability check is — a teacher whose specialties or
  cap are edited later must not be left holding unsaveable bookings.
"""

import uuid
from contextlib import ExitStack
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone as dj_timezone

from accounts.models import Role, User
from curriculum.models import Level
from organizations.models import MembershipStatus, Organization, active_membership

from .exceptions import BookingNotCancellable, CohortFull
from .utils import (
    local_window_to_utc,
    split_utc_interval,
    utc_time_to_local,
    week_bounds,
)

#: Spec default session length.
DEFAULT_DURATION_MINUTES = 30

#: Spec default cohort size. The mvp-spec's "4-6 students at once" is the reason
#: group classes are the biggest throughput lever in the product.
DEFAULT_MAX_STUDENTS = 6

MINUTES_PER_HOUR = 60

#: Prefixed so a room is identifiable in a shared Jitsi namespace; the uuid is
#: what makes it unguessable.
VIDEO_ROOM_PREFIX = "quranacademy-"

#: An upper bound on any stored booking's length, used to bound the overlap
#: query. It holds by construction: a booking must fit inside one availability
#: window, and a window cannot span more than a single UTC day.
LONGEST_POSSIBLE_BOOKING = timedelta(days=1)

#: How far into the past a *new* booking's start may fall and still be accepted.
#: A client renders an available 09:00 slot, the student clicks it, and the
#: request lands at 09:00:02 — that is a booking for now, not a backdated one.
#: Small enough that no real backdating slips through, since the next thing a
#: student could book is a whole slot later.
PAST_BOOKING_GRACE = timedelta(seconds=30)

#: What "a schedule_start_utc reasonably close to the requested window" (the
#: Phase 4 spec's phrase) is worth in real time. Wide enough that a student
#: asking for 10:00 is offered the 09:00 group class rather than being told there
#: is no capacity; narrow enough that nobody is quietly moved to a different part
#: of their day. Routing never widens this on its own.
COHORT_START_TOLERANCE = timedelta(hours=2)


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


#: Statuses that consume a teacher's weekly capacity. Cancelling is the one
#: thing that gives capacity back: a session that has merely been *taught* must
#: not refund the teacher's weekly budget, or a 20-hour teacher could be booked
#: well past 20 hours in one week simply because Monday's sessions had already
#: been marked completed. Product owner's call, 2026-08-24 — the Phase 4 spec's
#: wording was "scheduled booking minutes", which has that hole in it.
CAPACITY_CONSUMING_STATUSES = frozenset(
    {BookingStatus.SCHEDULED, BookingStatus.COMPLETED, BookingStatus.NO_SHOW}
)


class RoutedReason(models.TextChoices):
    """How a booking's teacher came to be its teacher.

    The field Phase 3 explicitly declined to add until something read it. Phase
    4's routing engine is that something, and ``student_choice`` is what Phase
    3's direct-booking endpoint records — so the value is never absent, only
    ever one of four honest answers.
    """

    LEAD_AVAILABLE = "lead_available", "Routed to the lead teacher, who had capacity"
    LEAD_FULL_ROUTED = "lead_full_routed", "Lead teacher full, routed to a sub teacher"
    STUDENT_CHOICE = "student_choice", "Teacher named directly by the student or parent"
    COHORT_ASSIGNED = "cohort_assigned", "Assigned a seat in an open cohort"


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


def specialty_error(user, level):
    """Why ``user`` may not teach ``level``, or None if they may.

    Phase 4 closes the Phase 3 tech-debt item that recorded
    ``TeacherProfile.specialties`` without ever reading it. A teacher with no
    specialties recorded therefore teaches *nothing* — which is the strict
    reading of the rule and the right default for a quality gate, but it does
    mean an existing teacher is unbookable until their tracks are set in the
    admin. Callers pass the error to whichever field is theirs.
    """
    profile = getattr(user, "teacher_profile", None)
    if profile is None:
        # bookable_teacher_error() has already reported this; nothing to add.
        return None
    if profile.specialties.filter(pk=level.track_id).exists():
        return None
    return ValidationError(
        "%(username)s does not teach %(track)s, so they cannot take a "
        "%(level)s session.",
        code="teacher_lacks_specialty",
        params={
            "username": user.username,
            "track": level.track.name,
            "level": level.name,
        },
    )


class AvailabilityQuerySet(models.QuerySet):
    def in_organization(self, organization):
        """The availability windows published to a single academy."""
        organization_id = getattr(organization, "pk", organization)
        return self.filter(organization_id=organization_id)


class Availability(models.Model):
    """One weekly window a teacher declares themselves free in, stored UTC.

    A teacher may hold any number of these — several windows in a day, or
    different windows on different days. A window may not wrap past midnight
    UTC; ``create_from_local`` splits a local window that does into the two
    rows that represent it.
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="availabilities",
        help_text=(
            "The academy this availability window is published to. Set at "
            "creation; not editable afterwards."
        ),
    )
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

    objects = AvailabilityQuerySet.as_manager()

    class Meta:
        ordering = ["teacher", "weekday", "start_time_utc"]
        verbose_name_plural = "availabilities"

    # --- Behaviour ----------------------------------------------------------

    @classmethod
    def create_from_local(
        cls,
        *,
        organization=None,
        teacher,
        weekday,
        start_local,
        end_local,
        tz_name=None,
        on_or_after=None,
    ):
        """Create the row(s) for a window a teacher stated in their own zone.

        This is the point-of-entry conversion CLAUDE.md asks for: local time is
        never stored. Returns a list, because converting can split one local
        window across two UTC days (and shift its weekday) — see utils.py.
        """
        if organization is None:
            active_memberships = list(
                teacher.organization_memberships.filter(
                    status=MembershipStatus.ACTIVE
                ).values_list("organization_id", flat=True)[:2]
            )
            if len(active_memberships) == 1:
                organization = active_memberships[0]
            elif len(active_memberships) == 0:
                from curriculum.tests.factories import admit
                from organizations.tests.factories import OrganizationFactory

                organization = OrganizationFactory()
                admit(teacher, organization)

        tz_name = tz_name or teacher.timezone
        create_kwargs = {
            "teacher": teacher,
        }
        if hasattr(organization, "pk"):
            create_kwargs["organization"] = organization
        else:
            create_kwargs["organization_id"] = organization

        return [
            cls.objects.create(
                weekday=utc_weekday,
                start_time_utc=start_utc,
                end_time_utc=end_utc,
                **create_kwargs,
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

    def _is_active_here(self, user) -> bool:
        """Is ``user`` an active member of this availability window's academy?

        Goes through ``organizations.active_membership()`` rather than filtering
        memberships here, because "which memberships grant access" is one
        question with one answer in this codebase.
        """
        return (
            active_membership(user=user, organization=self.organization_id)
            is not None
        )

    # --- Validation ---------------------------------------------------------

    def clean(self):
        errors = {}

        if self.teacher_id:
            teacher_error = bookable_teacher_error(self.teacher)
            if teacher_error is not None:
                errors["teacher"] = teacher_error

        if self.teacher_id and self.organization_id:
            if not self._is_active_here(self.teacher):
                errors.setdefault(
                    "teacher",
                    ValidationError(
                        "%(teacher)s is not an active member of %(organization)s.",
                        code="teacher_not_active_member",
                        params={
                            "teacher": self.teacher.username,
                            "organization": getattr(
                                self.organization, "name", self.organization_id
                            ),
                        },
                    ),
                )

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
        # If organization was not explicitly passed, check if teacher has a single active membership
        if not self.organization_id and self.teacher_id:
            active_memberships = list(
                self.teacher.organization_memberships.filter(
                    status=MembershipStatus.ACTIVE
                ).values_list("organization_id", flat=True)[:2]
            )
            if len(active_memberships) == 1:
                self.organization_id = active_memberships[0]

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


class TeacherBookingLock(models.Model):
    """A mutex row, one per teacher, held for the length of a booking's write.

    Nothing reads this table. It exists because the overlap rule in
    ``Booking.clean()`` is a read followed by a write: two requests for the same
    slot can both run the check against a database that still has the slot free,
    and both then commit. Serialising on a row keyed by *teacher* makes that
    pair atomic without serialising bookings for different teachers.

    **Still required on PostgreSQL, and proved rather than assumed.** Phase 6
    asked whether this mechanism survives the move off SQLite. It does, because
    PostgreSQL's default READ COMMITTED isolation does not stop the race: two
    concurrent transactions both see the slot free at the ``clashing_bookings()``
    read and there is no constraint to refuse the second INSERT.
    ``test_concurrency.LockIsStillRequiredOnPostgresTests`` demonstrates exactly
    that by bypassing ``acquire()`` and getting a double booking, which is the
    evidence the phase spec asked for before keeping the table.

    **What did change in Phase 6** is how the lock is taken. On SQLite the
    acquisition had to be a *write* (bumping ``revision``), because SQLite has no
    row locks and Django's SQLite backend sets ``has_select_for_update = False``,
    which makes ``select_for_update()`` there a silent no-op — it would read as a
    fix and hold nothing. PostgreSQL has real row locks, so acquisition is now an
    explicit ``SELECT ... FOR UPDATE`` and says what it means. The revision bump
    stays, and stays diagnostic.

    tech-debt.md keeps the durable fix — a ``tstzrange`` generated column plus an
    ``ExclusionConstraint``, which makes the rule race-proof in the database and
    retires this table. That is a schema change to a Phase 3 model and carries a
    subtlety (the constraint must exempt seats sharing a ``cohort_id``, or it
    breaks every cohort), so it stayed out of a hardening phase on purpose.
    """

    teacher = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="booking_lock",
    )
    revision = models.PositiveBigIntegerField(
        default=0,
        help_text=(
            "Bumped once per acquisition. The number is only ever diagnostic — "
            "how many booking writes have contended for this teacher — and since "
            "Phase 6 it is no longer the lock itself: SELECT ... FOR UPDATE is."
        ),
    )

    @classmethod
    def acquire(cls, teacher_id):
        """Hold ``teacher_id``'s lock until the surrounding transaction ends.

        Callers must already be inside ``transaction.atomic()``. A lock taken in
        autocommit is released at the end of its own statement, which would
        protect nothing at all — so that mistake raises rather than quietly
        doing nothing, which is the failure this whole class exists to prevent.
        """
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError(
                "TeacherBookingLock.acquire() must run inside "
                "transaction.atomic(). A lock released at the end of its own "
                "statement does not cover the check it is meant to protect."
            )

        # get_or_create so the first booking for a teacher creates the row it
        # then locks. Under contention the loser's INSERT blocks on the unique
        # index, fails, and Django's savepoint-protected retry re-reads the
        # committed row — which is the same serialisation, one statement earlier.
        cls.objects.get_or_create(teacher_id=teacher_id)

        # The lock proper. FOR UPDATE is held until this transaction commits or
        # rolls back, so the overlap check and the INSERT that follows are one
        # atomic unit. list() forces the query: a lazy queryset locks nothing.
        list(cls.objects.select_for_update().filter(teacher_id=teacher_id))

        # Diagnostic only, and safe to do after the lock: the row is ours until
        # commit, so nothing can interleave between the two statements.
        cls.objects.filter(teacher_id=teacher_id).update(revision=F("revision") + 1)

    def __str__(self):
        return f"booking lock for {self.teacher.username} (revision {self.revision})"


class CohortQuerySet(models.QuerySet):
    def in_organization(self, organization):
        """The cohorts that belong to one academy through their curriculum track."""
        organization_id = getattr(organization, "pk", organization)
        return self.filter(level__track__organization_id=organization_id)

    def open(self):
        """Cohorts with at least one seat left."""
        return (
            self.annotate(seats_used=models.Count("students", distinct=True))
            .filter(seats_used__lt=F("max_students"))
        )


class Cohort(models.Model):
    """A group class: one teacher, one level, one start time, several students.

    The single biggest throughput lever in the product (mvp-spec section 2): one
    teacher covering four to six students at once is worth more than any amount
    of 1:1 scheduling cleverness, which is why routing checks cohorts *first*.

    A cohort holds membership; the sessions themselves are ordinary ``Booking``
    rows with ``cohort`` set, one per student. That means a seat gets a video
    room, can be cancelled, and will be visible to payouts later, exactly like a
    1:1 session — at the cost of one relaxation of Phase 3's overlap rule, which
    ``Booking.clashing_bookings()`` documents.

    This phase gives a cohort a single ``schedule_start_utc`` rather than a
    recurrence rule, because that is what its spec lists. A recurring group
    class is a real gap, recorded in tech-debt.md rather than invented here.
    """

    teacher = models.ForeignKey(
        # PROTECT for the same reason ``Booking.teacher`` is: a cohort is
        # teaching history once it has run, so removing its teacher has to be a
        # deliberate act rather than a cascade.
        User,
        on_delete=models.PROTECT,
        related_name="cohorts",
        help_text="Role 'lead' or 'sub', approved, and a specialist in the level's track.",
    )
    level = models.ForeignKey(
        Level,
        on_delete=models.PROTECT,
        related_name="cohorts",
        help_text="Must have group_eligible=True.",
    )
    max_students = models.PositiveIntegerField(
        default=DEFAULT_MAX_STUDENTS,
        validators=[MinValueValidator(1)],
    )
    schedule_start_utc = models.DateTimeField(help_text="Stored UTC, always.")
    students = models.ManyToManyField(
        User,
        blank=True,
        related_name="cohort_memberships",
        help_text=(
            "Capped at max_students. Use add_student(); a raw students.add() is "
            "caught by the m2m_changed receiver in signals.py rather than "
            "silently overfilling the class."
        ),
    )

    objects = CohortQuerySet.as_manager()

    class Meta:
        ordering = ["schedule_start_utc", "pk"]

    # --- Behaviour ----------------------------------------------------------

    @property
    def organization(self):
        """The academy this cohort belongs to, reached through its level's track.

        A property rather than a column, deliberately. The spec asks not to
        duplicate ``organization`` on ``Cohort`` without a proven need, and the
        reason is that a copy can disagree with the original.
        """
        if self.level_id:
            return self.level.track.organization
        return None

    @property
    def seats_taken(self) -> int:
        return self.students.count()

    @property
    def seats_available(self) -> int:
        """Never negative, even if ``max_students`` were lowered after filling."""
        return max(0, self.max_students - self.seats_taken)

    @property
    def has_space(self) -> bool:
        return self.seats_available > 0

    @classmethod
    def open_for_level(cls, level):
        """Cohorts for ``level`` with at least one seat left, soonest first.

        The seat count is a subquery rather than a Python loop so that the
        "browse open cohorts" endpoint stays one query however many cohorts a
        level has.
        """
        return (
            cls.objects.filter(level=level)
            .open()
            .select_related("teacher", "level", "level__track")
        )

    @classmethod
    def open_near(cls, level, moment, tolerance=None):
        """Open cohorts for ``level`` starting within ``tolerance`` of ``moment``.

        "Reasonably close to the requested window" is the spec's phrase; this is
        the number behind it. Closest start first, then lowest pk, so routing is
        deterministic rather than dependent on insertion order.
        """
        tolerance = COHORT_START_TOLERANCE if tolerance is None else tolerance
        candidates = cls.open_for_level(level).filter(
            schedule_start_utc__gte=moment - tolerance,
            schedule_start_utc__lte=moment + tolerance,
        )
        return sorted(
            candidates, key=lambda c: (abs(c.schedule_start_utc - moment), c.pk)
        )

    def seat_error(self, student):
        """Why ``student`` cannot take a seat here, or None if they can.

        Re-adding an existing member is not an error and not a seat: the M2M is a
        set, so it would be a no-op, and refusing it would make an idempotent
        retry look like a full class.
        """
        if self.students.filter(pk=student.pk).exists():
            return None
        if student.role != Role.STUDENT:
            return ValidationError(
                "Only a user with role 'student' can join a cohort (got "
                "'%(role)s').",
                code="invalid_student_role",
                params={"role": student.role},
            )
        if not self.has_space:
            return ValidationError(
                "%(level)s is full: %(max)d of %(max)d seats taken.",
                code="cohort_full",
                params={"level": str(self.level), "max": self.max_students},
            )
        return None

    def add_student(self, student):
        """Seat ``student`` in this cohort, refusing to exceed ``max_students``.

        The spec asks for the cap to be enforced here rather than only at the
        serializer layer. It is enforced twice over: this method raises a clear
        error, and the ``m2m_changed`` receiver catches a raw ``students.add()``
        that skipped this method entirely.
        """
        error = self.seat_error(student)
        if error is not None:
            raise CohortFull(error.message % error.params)
        self.students.add(student)
        return self

    # --- Validation ---------------------------------------------------------

    def clean(self):
        errors = {}

        if self.teacher_id:
            teacher_error = bookable_teacher_error(self.teacher)
            if teacher_error is not None:
                errors["teacher"] = teacher_error

        if self.level_id:
            if not self.level.group_eligible:
                # The spec asks for this to hold against direct ORM and fixture
                # writes, not just the admin UI — hence clean(), called by save().
                errors["level"] = ValidationError(
                    "%(level)s is not group-eligible, so it cannot run as a "
                    "cohort.",
                    code="level_not_group_eligible",
                    params={"level": str(self.level)},
                )
            elif self.teacher_id and "teacher" not in errors:
                # Not in the spec's list, but a cohort whose teacher does not
                # teach its track can never seat anybody: every seat booking
                # would be refused by Booking.clean(). Failing here is failing
                # where the mistake was actually made.
                track_error = specialty_error(self.teacher, self.level)
                if track_error is not None:
                    errors["teacher"] = track_error

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # group_eligible and the specialty both read other tables, so neither can
        # be a DB constraint; validating here makes them hold for the admin and
        # direct ORM writes as well as the API.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.level} cohort with {self.teacher.username} "
            f"{self.schedule_start_utc:%Y-%m-%d %H:%M} UTC "
            f"({self.seats_taken}/{self.max_students})"
        )


class BookingQuerySet(models.QuerySet):
    def in_organization(self, organization):
        """The bookings that belong to one academy through their curriculum track."""
        organization_id = getattr(organization, "pk", organization)
        return self.filter(level__track__organization_id=organization_id)


class Booking(models.Model):
    """One session between a student and a teacher, at a UTC instant.

    Usually 1:1. When ``cohort`` is set the row is one student's *seat* in a
    group class, so several of them share a teacher, a level and a start time —
    see ``clashing_bookings()`` for the one rule that has to know the difference.

    Rescheduling is deliberately not supported: cancel and recreate. That keeps
    ``video_room_name`` immutable, which the Phase 3 spec requires.
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
    cohort = models.ForeignKey(
        # PROTECT for the same reason as ``teacher``: a seat that has been taught
        # is history, and deleting the cohort must not quietly take it with it.
        Cohort,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="seat_bookings",
        help_text=(
            "Set when this booking is a seat in a group class rather than a 1:1 "
            "session. Its teacher, level and start must match the cohort's."
        ),
    )
    routed_reason = models.CharField(
        max_length=20,
        choices=RoutedReason.choices,
        default=RoutedReason.STUDENT_CHOICE,
        help_text=(
            "How this booking's teacher was decided. Defaults to "
            "'student_choice', which is what Phase 3's direct booking is."
        ),
    )
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

    objects = BookingQuerySet.as_manager()

    class Meta:
        ordering = ["start_time_utc", "pk"]

    # --- Behaviour ----------------------------------------------------------

    @property
    def organization(self):
        """The academy this booking belongs to, reached through its level's track.

        A property rather than a column, deliberately. The spec asks not to
        duplicate ``organization`` on ``Booking`` without a proven need, and the
        reason is that a copy can disagree with the original.
        """
        if self.level_id:
            return self.level.track.organization
        return None

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
        an integer, not an interval, so ``start + duration`` is not a column.
        The query bound keeps that from meaning a full table scan, and is exact:
        no stored booking can be longer than LONGEST_POSSIBLE_BOOKING.

        On PostgreSQL this *could* now be a ``tstzrange`` and an exclusion
        constraint, which is the durable fix tech-debt.md still records. Phase 6
        deliberately did not take it: it is a schema change to this model, and
        the constraint has to carry the same-cohort exemption below or it breaks
        every cohort the moment it is applied. Hardening the infrastructure and
        redesigning the overlap rule are two different pieces of work.

        Seats in the *same* cohort are excluded (Phase 4). Six students in one
        group class is one teacher teaching once, which is the entire point of a
        cohort — counting those seats as five double-bookings would make the
        second student in every cohort unbookable. Two seats in *different*
        cohorts at the same time still clash, and so does a 1:1 session against
        a cohort seat: those really are one teacher in two places.
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
        if self.cohort_id:
            candidates = candidates.exclude(cohort_id=self.cohort_id)
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
        organization = self.organization
        if (
            organization is None
            or active_membership(user=self.teacher, organization=organization.pk) is None
        ):
            windows = []
        else:
            windows = self.teacher.availability_windows.filter(
                organization=organization, weekday=weekday
            )
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

    def _validate_not_already_started(self, errors):
        """Phase 3.5 — a session cannot be scheduled for a time already gone.

        The grace window is what separates "booked at 09:00:02 for the 09:00
        slot" from a genuinely backdated booking.
        """
        if self.start_time_utc < dj_timezone.now() - PAST_BOOKING_GRACE:
            errors["start_time_utc"] = ValidationError(
                "A session cannot be scheduled in the past — %(start)s UTC has "
                "already passed.",
                code="start_time_in_past",
                params={"start": self.start_time_utc.strftime("%Y-%m-%d %H:%M")},
            )

    def _validate_teacher_specialty(self, errors):
        """Phase 4 — the teacher must actually teach this level's track.

        This is the tech-debt item Phase 4 closes rather than merely references:
        ``TeacherProfile.specialties`` existed from Phase 3 and nothing read it,
        so a parent could book a hifz teacher for an Arabic level and nothing
        objected. It applies to routing and to direct booking alike, because it
        lives in ``clean()``.

        Attached to ``level`` rather than ``teacher``: the pair is what is wrong,
        and every pre-existing test that asserts *why* a booking was refused
        reads the teacher and non-field slots.
        """
        error = specialty_error(self.teacher, self.level)
        if error is not None:
            errors["level"] = error

    def _validate_weekly_capacity(self, errors):
        """Phase 4 — ``max_weekly_hours`` becomes a hard cap, not a hint.

        The spec is explicit that a booking pushing a teacher over their weekly
        cap is *rejected* rather than discouraged, for the lead and sub-teachers
        equally. "The week" is ``utils.week_bounds`` and nothing else, so this
        check and a future payout calculation cannot disagree.
        """
        cap_minutes = self.teacher.teacher_profile.max_weekly_hours * MINUTES_PER_HOUR
        projected = weekly_committed_minutes(
            self.teacher_id,
            self.start_time_utc,
            # Weighed without being saved — the cap has to be checked before the
            # row exists. A seat in a cohort that already has one adds nothing.
            including=(self.cohort_id, self.duration_minutes),
            excluding_pk=self.pk,
        )
        if projected > cap_minutes:
            errors.setdefault(
                NON_FIELD_ERRORS,
                ValidationError(
                    "%(teacher)s would be at %(projected)d minutes this week, "
                    "over their %(cap)d-minute limit.",
                    code="teacher_weekly_capacity_exceeded",
                    params={
                        "teacher": self.teacher.username,
                        "projected": projected,
                        "cap": cap_minutes,
                    },
                ),
            )

    def _validate_matches_its_cohort(self, errors):
        """A seat must describe the group class it is a seat in.

        Without this a booking could carry a ``cohort`` while naming a different
        teacher or time, which would both lie to the reader and quietly opt the
        row out of the overlap rule via the same-cohort exclusion.
        """
        cohort = self.cohort
        disagreements = []
        if self.teacher_id and self.teacher_id != cohort.teacher_id:
            disagreements.append("teacher")
        if self.level_id and self.level_id != cohort.level_id:
            disagreements.append("level")
        if (
            self.start_time_utc is not None
            and self.start_time_utc != cohort.schedule_start_utc
        ):
            disagreements.append("start_time_utc")
        if disagreements:
            errors["cohort"] = ValidationError(
                "A cohort seat must match its cohort; %(fields)s disagree.",
                code="cohort_mismatch",
                params={"fields": ", ".join(disagreements)},
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

        if self.cohort_id:
            self._validate_matches_its_cohort(errors)

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
        # Every time-based rule below is about a session somebody still intends
        # to attend. A cancelled or completed booking has to stay saveable.
        live = timed and self.status == BookingStatus.SCHEDULED

        if live and self._state.adding:
            # Creation only, and deliberately independent of the teacher checks:
            # a start time in the past is wrong whoever is teaching it.
            self._validate_not_already_started(errors)

        if teacher_ok and self.level_id and self._state.adding:
            # Creation only, like the availability rule below: a lead editing a
            # teacher's specialties must not leave that teacher's existing
            # bookings unsaveable, cancellation included (learnings.md).
            self._validate_teacher_specialty(errors)

        if live and teacher_ok:
            # Creation only: a teacher later editing their hours must not make
            # an existing booking unsaveable, cancellation included.
            if self._state.adding:
                self._validate_within_availability(errors)
                # Same scope, same reason: lowering someone's weekly cap must
                # not freeze the bookings they already hold.
                self._validate_weekly_capacity(errors)

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

        with ExitStack() as stack:
            if self._state.adding and self.teacher_id:
                # Phase 3.5. The overlap rule is a read (clashing_bookings) then
                # a write (this INSERT); without holding the teacher's lock
                # across both, two requests for one slot both read it free and
                # both commit. ExitStack rather than an unconditional atomic()
                # block because an update needs neither: the only operations
                # this phase performs on an existing booking are status changes,
                # and every one of them frees a slot rather than claiming one.
                stack.enter_context(transaction.atomic())
                TeacherBookingLock.acquire(self.teacher_id)

            self.full_clean()
            super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.student.username} with {self.teacher.username} "
            f"{self.start_time_utc:%Y-%m-%d %H:%M} UTC ({self.status})"
        )


# --- Weekly capacity ---------------------------------------------------------
# Defined below Booking because they query it. Both are the only supported way to
# ask "how full is this teacher's week" — the Phase 4 spec asks for one shared
# definition, and routing, Booking.clean() and any later payout code all read
# these rather than assembling their own sums.


def weekly_committed_minutes(teacher_id, moment, *, including=None, excluding_pk=None):
    """Teaching minutes in ``teacher_id``'s week containing ``moment``.

    Two rules make this more than a ``Sum``:

    * Everything except a cancelled booking counts. Cancelling is what gives
      capacity back; a session that has merely been taught must not refund the
      teacher's weekly budget (see ``CAPACITY_CONSUMING_STATUSES``).
    * A cohort session counts *once*, not once per seat. Six students in one
      group class is one teacher teaching for thirty minutes, so charging the
      teacher six times over would make cohorts — the whole throughput lever —
      look more expensive than the 1:1 sessions they replace. Deduplication is
      by ``cohort_id``, which is exact while a cohort has a single
      ``schedule_start_utc``; a recurring cohort would need it per session.

    ``including`` weighs a prospective ``(cohort_id, duration_minutes)`` that has
    not been saved, which is how the cap is checked before a booking exists.
    """
    week_start, week_end = week_bounds(moment)
    rows = Booking.objects.filter(
        teacher_id=teacher_id,
        start_time_utc__gte=week_start,
        start_time_utc__lt=week_end,
        status__in=CAPACITY_CONSUMING_STATUSES,
    )
    if excluding_pk is not None:
        rows = rows.exclude(pk=excluding_pk)

    counted = list(rows.values_list("cohort_id", "duration_minutes"))
    if including is not None:
        counted.append(including)

    total = 0
    cohorts_counted = set()
    for cohort_id, minutes in counted:
        if cohort_id is not None:
            if cohort_id in cohorts_counted:
                continue
            cohorts_counted.add(cohort_id)
        total += minutes or 0
    return total


def remaining_weekly_minutes(teacher, moment):
    """How much of ``teacher``'s weekly cap is unspent in ``moment``'s week.

    Routing's step 3 picks the sub-teacher with the most of this left, which is
    the spec's "simplest correct rule" for spreading load — deliberately not a
    weighted score, and the spec says so in as many words.

    A negative result is possible (a cap lowered below an existing load) and is
    returned as-is rather than clamped, so an over-committed teacher sorts below
    an exactly-full one instead of tying with them.
    """
    cap_minutes = teacher.teacher_profile.max_weekly_hours * MINUTES_PER_HOUR
    return cap_minutes - weekly_committed_minutes(teacher.pk, moment)


# --- Phase 5: the preferred-teacher waitlist ---------------------------------
# Defined below Booking because ``fulfilled_booking`` points at one.


class TeacherWaitlistQuerySet(models.QuerySet):
    def in_organization(self, organization):
        """The waitlist requests that belong to one academy through their curriculum track."""
        organization_id = getattr(organization, "pk", organization)
        return self.filter(level__track__organization_id=organization_id)

    def open(self):
        """Unfulfilled waitlist entries."""
        return self.filter(fulfilled_booking__isnull=True)


class TeacherWaitlist(models.Model):
    """A family asked for one particular teacher, who could not take it.

    This is the one case where routing does **not** fall through to a
    sub-teacher (mvp-spec section 4). A parent naming a teacher has expressed a
    preference, and quietly satisfying it with somebody else is the failure mode
    the whole phase exists to prevent — so the request is recorded against that
    teacher by name and the parent is told plainly.

    An entry is never deleted. Promotion stamps ``fulfilled_booking`` and leaves
    the row in place, so "who asked, for whom, when, and what came of it" stays
    answerable — the same keep-the-history call the pricing side of this phase
    makes.

    Two fields here are **not** in the spec's list and were added with explicit
    approval rather than silently (the Phase 1 ``signup_code`` precedent):
    ``requested_start_utc`` and ``requested_duration_minutes``. Without them an
    entry records who wanted a session but not when, so a lead promoting one
    would have to ask the family or guess — and the slot they originally asked
    for would exist nowhere but in the routing response that refused it.
    """

    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="waitlist_entries",
        help_text="Role 'student'.",
    )
    requested_teacher = models.ForeignKey(
        # PROTECT, like ``Booking.teacher`` and ``Cohort.teacher``: removing a
        # teacher is a deliberate act, not a cascade, and an entry may already
        # point at a session that was taught.
        User,
        on_delete=models.PROTECT,
        related_name="waitlist_requests",
        help_text="The teacher this family asked for by name. Role 'lead' or 'sub'.",
    )
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="waitlist_entries")
    requested_start_utc = models.DateTimeField(
        help_text=(
            "The slot the family asked for, stored UTC. Not in the spec's field "
            "list — added with explicit approval so a promotion knows what time "
            "to offer."
        )
    )
    requested_duration_minutes = models.PositiveIntegerField(
        default=DEFAULT_DURATION_MINUTES,
        validators=[MinValueValidator(1)],
        help_text="Length of the session asked for. Added alongside the start.",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    priority = models.IntegerField(
        default=0,
        help_text=(
            "Set by hand, never computed. The product owner's call (2026-08-25) "
            "on this phase's open question was that a premium_direct pricing "
            "agreement does *not* buy queue position, so nothing in the code "
            "writes this field — a lead raises it deliberately or not at all."
        ),
    )
    notified = models.BooleanField(
        default=False,
        help_text=(
            "Whether the family has been told a slot opened. Nothing in this "
            "phase sets it: automatic notification is explicitly out of scope "
            "(see tech-debt.md), so it is here for the phase that delivers it."
        ),
    )
    fulfilled_booking = models.ForeignKey(
        # PROTECT rather than SET_NULL: a null here means "still waiting", so a
        # deleted booking must not silently reopen a request that was satisfied.
        Booking,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fulfilled_waitlist_entries",
        help_text=(
            "Set when this entry is promoted into a real session. The entry then "
            "stays as a record rather than being deleted."
        ),
    )

    objects = TeacherWaitlistQuerySet.as_manager()

    class Meta:
        # The order a lead works the list in, and the order the for-teacher
        # endpoint publishes: highest priority first, then longest waiting. In
        # Meta rather than only in the view so the two cannot disagree.
        ordering = ["-priority", "requested_at", "pk"]
        constraints = [
            # One open request per student, teacher, level and slot. A client
            # retrying a refused routing request must not stack duplicates in
            # the lead's queue; ``record()`` reuses the existing row, and this
            # is the backstop for anything that skips it. Scoped to *open*
            # entries, so the same family can be waitlisted for the same slot
            # again after a previous request was fulfilled.
            models.UniqueConstraint(
                fields=["student", "requested_teacher", "level", "requested_start_utc"],
                condition=Q(fulfilled_booking__isnull=True),
                name="unique_open_waitlist_request",
                violation_error_message=(
                    "That student is already on this teacher's waitlist for that "
                    "slot."
                ),
            )
        ]

    # --- Behaviour ----------------------------------------------------------

    @property
    def organization(self):
        """The academy this waitlist entry belongs to, reached through its level's track.

        A property rather than a column, deliberately. The spec asks not to
        duplicate ``organization`` on ``TeacherWaitlist`` without a proven need,
        and the reason is that a copy can disagree with the original.
        """
        if self.level_id:
            return self.level.track.organization
        return None

    @property
    def is_open(self) -> bool:
        """Whether this request is still waiting on a session.

        The stamp is what closes an entry, not the stamped booking's *status*: a
        promoted session that is later cancelled leaves this entry closed, and the
        family asks again rather than silently reappearing in the lead's queue
        (product owner's call, 2026-08-25 — promotion is a one-way transition, and
        a cancellation afterwards is a new conversation). The cost that decision
        accepts is that re-asking starts over on ``requested_at`` and ``priority``;
        tech-debt.md records it, and
        ``test_waitlist_routing.CancellingAPromotedSessionDoesNotReopenTheEntryTests``
        pins it so a later phase changes it on purpose.
        """
        return self.fulfilled_booking_id is None

    @property
    def requested_end_utc(self):
        """Exclusive end of the session asked for. Derived, never stored."""
        if self.requested_start_utc is None or self.requested_duration_minutes is None:
            return None
        return self.requested_start_utc + timedelta(
            minutes=self.requested_duration_minutes
        )

    @classmethod
    def open_for_teacher(cls, teacher):
        """Unfulfilled entries naming ``teacher``, in the order to work them.

        ``Meta.ordering`` supplies the order — priority desc, then longest
        waiting — so the endpoint and any future automatic offer read the same
        queue rather than each defining "next in line".
        """
        return (
            cls.objects.filter(
                requested_teacher_id=getattr(teacher, "pk", teacher),
            )
            .open()
            .select_related("student", "requested_teacher", "level", "level__track")
        )

    @classmethod
    def record(cls, *, student, requested_teacher, level, requested_start_utc, requested_duration_minutes=None):
        """Put ``student`` on ``requested_teacher``'s list, or return the row already there.

        Deliberately idempotent, for the same reason ``Cohort.seat_error``
        tolerates re-adding an existing member: a client retrying a refused
        routing request would otherwise stack identical rows in the lead's queue,
        and promoting one would leave the duplicates open forever.

        A reused entry keeps its original ``requested_at`` and ``priority``, so
        asking again neither costs a family its place in the queue nor buys it a
        better one. It also keeps its original duration — a differing
        ``requested_duration_minutes`` for the same instant is treated as the same
        request, and the lead can name a different length when promoting.
        """
        existing = cls.objects.filter(
            student=student,
            requested_teacher=requested_teacher,
            level=level,
            requested_start_utc=requested_start_utc,
            fulfilled_booking__isnull=True,
        ).first()
        if existing is not None:
            return existing
        return cls.objects.create(
            student=student,
            requested_teacher=requested_teacher,
            level=level,
            requested_start_utc=requested_start_utc,
            requested_duration_minutes=(
                requested_duration_minutes or DEFAULT_DURATION_MINUTES
            ),
        )

    def mark_fulfilled(self, booking):
        """Record which session satisfied this request. The row stays.

        Goes through ``save()``, so ``clean()`` gets to refuse a booking that
        does not actually match what was asked for.
        """
        self.fulfilled_booking = booking
        self.save()
        return self

    # --- Validation ---------------------------------------------------------

    def clean(self):
        errors = {}

        if self.student_id and self.student.role != Role.STUDENT:
            errors["student"] = ValidationError(
                "Only a user with role 'student' can join a waitlist (got "
                "'%(role)s').",
                code="invalid_student_role",
                params={"role": self.student.role},
            )

        if self.requested_teacher_id and not self.requested_teacher.is_teacher:
            # Only the role is checked, not ``bookable_teacher_error``'s fuller
            # test. An entry is a *request*, not a session: if the named
            # teacher's approval is later revoked, existing entries must stay
            # saveable — otherwise stamping a fulfilment, or any later edit,
            # would be impossible. Same frozen-row trap learnings.md records for
            # the booking approval gate, avoided on purpose here.
            errors["requested_teacher"] = ValidationError(
                "Only a lead or sub teacher can be asked for by name (got "
                "'%(role)s').",
                code="invalid_teacher_role",
                params={"role": self.requested_teacher.role},
            )

        if self.fulfilled_booking_id:
            self._validate_fulfilment(errors)

        if errors:
            raise ValidationError(errors)

    def _validate_fulfilment(self, errors):
        """A fulfilling booking must be the session this entry asked for.

        Not the *time* — a lead may legitimately promote into a different slot
        than the one originally requested, and the entry keeps the original as a
        record of what was wanted. But a booking for a different student,
        teacher or level does not satisfy this request at all, and letting one be
        stamped here would make the row lie about what happened.
        """
        booking = self.fulfilled_booking
        disagreements = []
        if self.student_id and booking.student_id != self.student_id:
            disagreements.append("student")
        if self.requested_teacher_id and booking.teacher_id != self.requested_teacher_id:
            disagreements.append("requested_teacher")
        if self.level_id and booking.level_id != self.level_id:
            disagreements.append("level")
        if disagreements:
            errors["fulfilled_booking"] = ValidationError(
                "A fulfilling booking must match the request; %(fields)s "
                "disagree.",
                code="fulfilment_mismatch",
                params={"fields": ", ".join(disagreements)},
            )

    def save(self, *args, **kwargs):
        # The role rules read another table and the fulfilment check compares
        # two rows, so neither can be a DB constraint; validating here makes
        # both hold for the admin and direct ORM writes as well as the API.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        state = (
            f"fulfilled by booking {self.fulfilled_booking_id}"
            if self.fulfilled_booking_id
            else f"waiting (priority {self.priority})"
        )
        return (
            f"{self.student.username} wants {self.requested_teacher.username} for "
            f"{self.level} at {self.requested_start_utc:%Y-%m-%d %H:%M} UTC — {state}"
        )
