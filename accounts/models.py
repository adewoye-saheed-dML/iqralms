"""Identity layer for the academy: users, parent links, teacher profiles.

Field sets here mirror specs/phase-1-accounts.md exactly. The one addition is
User.signup_code, which was agreed explicitly as the parent-link mechanism
(see learnings.md). Nothing about curriculum, booking or payment lives here.
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
    """Teaching-side attributes. Only for role 'lead' or 'sub'."""

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
