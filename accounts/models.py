"""Identity layer for the academy: users, parent links, teacher profiles.

Field sets here mirror specs/phase-1-accounts.md, plus three additions that were
each agreed explicitly rather than added silently:

* ``User.signup_code``, the parent-link mechanism (see learnings.md).
* ``TeacherProfile.specialties``, added by Phase 3 so booking can eventually
  know which tracks a teacher may teach. Nothing enforces it yet.
* ``OrganizationTeacherConfiguration``, added by SaaS Phase 2 so the same teacher
  can work for two academies on different terms.

**What stays global, and what became per-academy.** ``User`` is one identity for
one person, and it gains no ``organization`` foreign key — a field like that would
hard-code "one user, one academy" into the model the whole platform points at.
``ParentLink`` likewise stays a global family relationship. What is
*organization-specific* is how a teacher operates inside a given academy — their
approval, capacity and rate — and that is the new model rather than a change to
the old one, because ``TeacherProfile`` is still what scheduling and payouts read
(see its docstring).

Nothing about curriculum, booking or payment behaviour lives here.
"""

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone as dj_timezone

from .utils import generate_unique_signup_code
from .validators import validate_iana_timezone

MINOR_AGE = 18


class Role(models.TextChoices):
    LEAD = "lead", "Lead teacher"
    SUB = "sub", "Sub teacher"
    STUDENT = "student", "Student"
    PARENT = "parent", "Parent"


#: Roles that may hold a TeacherProfile.
TEACHER_ROLES = frozenset({Role.LEAD.value, Role.SUB.value})


class User(AbstractUser):
    """Every later phase points a foreign key at this model."""

    role = models.CharField(max_length=16, choices=Role.choices)
    timezone = models.CharField(
        max_length=64,
        validators=[validate_iana_timezone],
        help_text="IANA zone name, e.g. 'Africa/Lagos'. Required at signup.",
    )
    date_of_birth = models.DateField(null=True, blank=True)
    is_minor = models.BooleanField(
        default=False,
        help_text=(
            "Snapshot computed from date_of_birth at signup. Not recomputed on "
            "birthdays — see learnings.md."
        ),
    )
    signup_code = models.CharField(
        max_length=8,
        unique=True,
        blank=True,
        help_text=(
            "Opaque code a student shares with a parent so the parent can link "
            "to them. Generated automatically at signup."
        ),
    )

    # Prompted for by createsuperuser, on top of AbstractUser's 'email'.
    REQUIRED_FIELDS = ["email", "role", "timezone"]

    @staticmethod
    def minor_from_date_of_birth(date_of_birth, today=None) -> bool:
        """True when ``date_of_birth`` is under MINOR_AGE as of ``today``."""
        if date_of_birth is None:
            return False
        today = today or dj_timezone.localdate()
        age = (
            today.year
            - date_of_birth.year
            - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
        )
        return age < MINOR_AGE

    @property
    def is_teacher(self) -> bool:
        return self.role in TEACHER_ROLES

    @property
    def is_fully_active(self) -> bool:
        """Academy-level usability, deliberately distinct from ``is_active``.

        ``is_active`` governs whether Django lets the account authenticate. This
        governs whether the account is complete enough to be used (booking, in a
        later phase). A minor student is not complete until a parent is linked.
        """
        if self.role == Role.STUDENT and self.is_minor:
            return self.parent_links.exists()
        return True

    def save(self, *args, **kwargs):
        if not self.signup_code:
            self.signup_code = generate_unique_signup_code(type(self))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.username} ({self.role})"


class ParentLink(models.Model):
    """A parent's guardianship of a student account.

    Many-to-many in practice: a student may have several parents linked and a
    parent several children.
    """

    parent = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="child_links",
    )
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="parent_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "student"],
                name="unique_parent_student_link",
            )
        ]

    def clean(self):
        errors = {}
        if self.parent_id and self.parent.role != Role.PARENT:
            errors["parent"] = ValidationError(
                "Only a user with role 'parent' can be the parent of a link.",
                code="invalid_parent_role",
            )
        if self.student_id and self.student.role != Role.STUDENT:
            errors["student"] = ValidationError(
                "Only a user with role 'student' can be the student of a link.",
                code="invalid_student_role",
            )
        if self.parent_id and self.parent_id == self.student_id:
            errors[NON_FIELD_ERRORS] = ValidationError(
                "A user cannot be linked to themselves.",
                code="self_link",
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Role validity spans two tables, so it cannot be a DB constraint.
        # Validating in save() makes the rule hold for the API, the admin and
        # direct ORM writes alike.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.parent} -> {self.student}"


class TeacherProfile(models.Model):
    """Teaching-side attributes. Only for role 'lead' or 'sub'.

    **Still the global profile, and still authoritative.** SaaS Phase 2 added
    ``OrganizationTeacherConfiguration`` beside this model rather than moving
    fields out of it, because every existing consumer reads *this* one:
    ``scheduling.models.bookable_teacher_error`` and ``specialty_error``,
    ``scheduling.routing.lead_teacher`` and ``matching_sub_teachers``, the weekly
    capacity cap in ``Booking``/``route_session``, and
    ``payouts.services.applicable_rate``. Phase 2 is explicitly forbidden from
    rewriting scheduling or payout behaviour, so this stays the row they read and
    the new model is written but not yet consulted by them.

    The two therefore overlap on ``approved``, ``max_weekly_hours`` and
    ``hourly_payout_rate`` for as long as the migration takes. That is a stated
    interim state, not an oversight — see tech-debt.md.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="teacher_profile",
    )
    bio = models.TextField(blank=True)
    max_weekly_hours = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Capacity dial used by scheduling in a later phase.",
    )
    hourly_payout_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Null for the lead teacher, who is not paid a per-hour rate.",
    )
    is_lead = models.BooleanField(default=False)
    approved = models.BooleanField(
        default=False,
        help_text="A sub-teacher is not bookable until a lead flips this true.",
    )
    specialties = models.ManyToManyField(
        # String reference, not an import: curriculum imports this module, so a
        # real import would be circular. Added in Phase 3 with explicit
        # approval — it is a change to an already-shipped model.
        "curriculum.Track",
        blank=True,
        related_name="specialist_teachers",
        help_text=(
            "Tracks this teacher is eligible to teach. Recorded now, not "
            "enforced: Phase 4's routing is what matches a level's track "
            "against this (see tech-debt.md)."
        ),
    )

    def clean(self):
        errors = {}
        if self.user_id:
            if not self.user.is_teacher:
                errors["user"] = ValidationError(
                    "Only users with role 'lead' or 'sub' can have a teacher "
                    "profile (got '%(role)s').",
                    code="invalid_role_for_teacher_profile",
                    params={"role": self.user.role},
                )
            elif self.is_lead != (self.user.role == Role.LEAD):
                # is_lead duplicates User.role; keep them from contradicting
                # each other. See learnings.md.
                errors["is_lead"] = ValidationError(
                    "is_lead must be True exactly when the user's role is "
                    "'lead' (role is '%(role)s').",
                    code="is_lead_role_mismatch",
                    params={"role": self.user.role},
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"TeacherProfile({self.user.username})"


class OrganizationTeacherConfiguration(models.Model):
    """How one teacher operates inside one academy: approved, capacity, rate.

    The model SaaS Phase 2 exists to establish. A teacher is one ``User`` and may
    work for several academies on entirely different terms:

    .. code-block:: text

        Teacher T
            Academy A  ->  approved, 10 h/week, 10.00/hour
            Academy B  ->  not approved, 20 h/week, 15.00/hour

    A ``OneToOneField(User)`` cannot represent that, and duplicating the ``User``
    to make it fit would break the one thing the identity model guarantees. So the
    configuration hangs off the *membership* — which already means "this user in
    this academy", already carries the uniqueness rule for that pair, and already
    knows whether the relationship is active. A ``(user, organization)`` pair here
    would have been a second, weaker copy of ``OrganizationMembership``.

    **Which fields are per-academy, and which are not.** Approval, weekly capacity
    and payout rate describe how a teacher works *for one academy*, so they are
    here. ``bio`` describes the person and stays on ``TeacherProfile``.
    ``is_lead`` is deliberately *not* copied: academy leadership is already
    ``OrganizationMembership.role``, and a third representation of it — after
    ``User.role`` and ``TeacherProfile.is_lead``, which are validated to agree —
    would be one more thing to keep in step. ``specialties`` is untouched, because
    it points at ``curriculum.Track``, which is still global until curriculum
    tenancy (see learnings.md).

    **Nothing reads this yet.** Scheduling and payouts still read
    ``TeacherProfile``; their own tenancy phases move them across. This model is
    the storage boundary being put in place first, so those phases have somewhere
    to read *from* rather than having to invent it while also rewriting booking or
    payroll.

    A suspended membership keeps its configuration. The row is a record of the
    terms this academy set; whether it grants access is
    ``organizations.active_membership()``'s answer, not this model's.
    """

    membership = models.OneToOneField(
        # String reference rather than an import: ``organizations.models`` imports
        # this module, so a real import would be circular. The same pattern
        # ``TeacherProfile.specialties`` uses for ``curriculum.Track``.
        "organizations.OrganizationMembership",
        # The configuration is meaningless without the relationship it configures.
        # Nothing in the API deletes a membership — suspension keeps the row — so
        # this cascade only fires when an organization itself is removed.
        on_delete=models.CASCADE,
        related_name="teacher_configuration",
        help_text="The academy-and-teacher relationship these terms apply to.",
    )
    max_weekly_hours = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text=(
            "Capacity dial for this academy only. Scheduling still enforces "
            "TeacherProfile.max_weekly_hours until scheduling tenancy lands."
        ),
    )
    hourly_payout_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "What this academy pays per hour. Null means no per-hour rate, as on "
            "TeacherProfile. Payout calculation still reads that one."
        ),
    )
    approved = models.BooleanField(
        default=False,
        help_text=(
            "Approved to teach *here*. An academy approves its own teachers, so a "
            "teacher may be approved by one and not another."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["membership_id"]

    @property
    def user(self):
        """The teacher these terms are for. Reached through the membership."""
        return self.membership.user

    @property
    def organization(self):
        """The academy that set these terms."""
        return self.membership.organization

    def clean(self):
        # The same rule TeacherProfile enforces, and deliberately the same rule:
        # only a 'lead' or 'sub' account can be configured to teach. Phase 2 was
        # asked to preserve existing product behaviour rather than let an
        # organization invent a teaching identity the account model does not
        # support, so a parent or student membership is refused here even when the
        # organization is happy to call them a teacher.
        #
        # Note what is *not* checked: OrganizationMembership.role. That field is
        # authority inside the academy, and requiring 'teacher' would lock out the
        # lead teacher who founded their own academy and therefore holds the
        # 'owner' row (see accounts/tenancy.py).
        if self.membership_id and not self.membership.user.is_teacher:
            raise ValidationError(
                {
                    "membership": ValidationError(
                        "Only a member whose account role is 'lead' or 'sub' can "
                        "be configured to teach (got '%(role)s').",
                        code="invalid_role_for_teacher_configuration",
                        params={"role": self.membership.user.role},
                    )
                }
            )

    def save(self, *args, **kwargs):
        # The repository convention: validate in save() so the API, the admin and a
        # direct ORM write cannot disagree about what a valid row is.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.membership.user.username} @ {self.membership.organization.slug}"
