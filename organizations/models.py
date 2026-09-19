"""The tenant boundary: an academy, and who belongs to it.

Field sets mirror ``specs/saas/SaaS Phase 1 — Organization Foundation.md``. Two
models, and the distinction between them is the entire point of the phase:

.. code-block:: text

    User                    =  global identity
    OrganizationMembership  =  one user's relationship with one academy

``accounts.User.role`` is deliberately *not* reused as the organization role.
``lead``/``sub``/``student``/``parent`` answers "what is this person to the
teaching business", and it already drives booking, pricing, assessment and payout
behaviour. ``OrganizationRole`` answers a different question — "what authority
does this person have inside this tenant" — so the two are allowed to disagree,
and a ``student`` who creates an academy is its ``owner`` while remaining a
student. Nothing in this module reads or writes ``User.role``.

**Domain Architecture & Tenant Boundaries:**
* ``Organization`` and ``OrganizationMembership`` establish the foundational tenant and access model.
* Subsequent domain migrations attached tenant boundaries to domain objects (e.g., ``Track.organization``, ``Availability.organization``, ``StudentEnrollment.organization``, and canonical properties on ``Level``, ``Booking``, ``Cohort``, ``TeacherWaitlist``).
* Membership access (``active_membership()``) is the single source of truth for organization permission and authorization across all domain operations.
* ``accounts.OrganizationTeacherConfiguration`` and ``curriculum.TeacherTrack`` hang off ``OrganizationMembership`` to provide tenant-scoped teacher configuration and eligibility.

Ownership is a *membership*, never a second field on ``Organization``. One source
of truth: the owner is the row whose role is ``owner``, and the database holds
"at most one of those per organization" as a constraint rather than trusting the
code that writes it.

**Membership vs Enrollment (B02):**

    Membership means *what authority a person holds inside this academy*;
    enrollment means *which academic programme a student is placed in here*.

Membership is the tenant-access relation: it decides whether a user may act inside
an academy and as what role (owner, admin, staff, teacher). Enrollment is the
academic-placement relation: it records which student is studying which track at
which level inside an academy. A student who is enrolled is not automatically a
membership-level participant, and a staff member who holds a membership is not
automatically a student. The two answer different questions and neither implies the
other.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from accounts.models import User
from accounts.validators import validate_iana_timezone


class OrganizationRole(models.TextChoices):
    """Authority inside one academy. Not ``accounts.Role`` — see the module docstring.

    Four levels, and the gap between ``admin`` and ``owner`` is the one that
    matters in Phase 1: both manage memberships, but only the owner's row *is*
    the ownership, and no endpoint here can create, move or demote it. Ownership
    transfer is a named later decision.
    """

    OWNER = "owner", "Owner"
    ADMIN = "admin", "Administrator"
    STAFF = "staff", "Staff"
    TEACHER = "teacher", "Teacher"


class MembershipStatus(models.TextChoices):
    """The whole membership lifecycle. Two states, on purpose.

    ``suspended`` disables tenant access while keeping the row, which is why no
    endpoint deletes a membership. There is deliberately no ``pending`` or
    ``invited``: an invitation needs onboarding, token and delivery decisions that
    the spec assigns to a later phase, and a lifecycle state added now would have
    to be reinterpreted when those decisions are actually made.
    """

    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


#: Organization roles that may manage the tenant's membership list. Owner and
#: admin do the same job in Phase 1 — the difference between them is what may be
#: done *to* the owner's own row, which ``permissions.OwnerMembershipIsProtected``
#: answers rather than this set. A frozenset rather than a bare comparison so
#: widening it later is one visible edit.
MEMBERSHIP_MANAGER_ROLES = frozenset({OrganizationRole.OWNER, OrganizationRole.ADMIN})


class Organization(models.Model):
    """One independent academy operating on the platform. The tenant itself.

    Nothing points at this model yet except ``OrganizationMembership``. That is
    the phase boundary, not an oversight: making curriculum, scheduling, pricing,
    assessment and payouts organization-scoped is a sequence of later phases, each
    of which has to decide what its own existing uniqueness rules mean per tenant.
    """

    name = models.CharField(
        max_length=120,
        help_text=(
            "Human-readable institution name, e.g. 'Al-Huda Quran Academy'. Not "
            "unique — two academies may legitimately choose similar names."
        ),
    )
    slug = models.SlugField(
        max_length=64,
        unique=True,
        help_text=(
            "Machine-readable identifier, e.g. 'al-huda-quran-academy'. Globally "
            "unique, because the organization is itself the tenant and so has "
            "nothing to be scoped within."
        ),
    )
    timezone = models.CharField(
        max_length=64,
        validators=[validate_iana_timezone],
        help_text=(
            "IANA zone name, e.g. 'Africa/Lagos'. The academy's own zone, "
            "validated by the same rule as User.timezone."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Whether the academy is operating. Disabling an organization is a "
            "later phase's workflow, so nothing reads this yet — access is "
            "decided by active membership alone (see learnings.md)."
        ),
    )
    VIDEO_PROVIDER_CHOICES = [
        ("jitsi", "Jitsi"),
    ]
    video_provider = models.CharField(
        max_length=32,
        choices=VIDEO_PROVIDER_CHOICES,
        default="jitsi",
        help_text="The preferred video provider for this academy."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]

    @property
    def owner_membership(self):
        """The one membership that owns this organization, or None.

        There is no ``Organization.owner`` field to disagree with this, which is
        the point: ownership has a single representation, and this reads it.
        """
        return self.memberships.filter(role=OrganizationRole.OWNER).first()

    def clean(self):
        # CharField's blank check does not see a name made of spaces, and an
        # academy called "   " is not named. Normalizing here rather than in the
        # serializer keeps the rule true for the admin and direct ORM writes too.
        if self.name:
            self.name = self.name.strip()
        if not self.name:
            raise ValidationError(
                {
                    "name": ValidationError(
                        "An organization needs a name.", code="blank"
                    )
                }
            )

    def save(self, *args, **kwargs):
        # The repository convention (ParentLink, TeacherProfile, PricingAgreement,
        # TeacherPayout): validate in save() so the API, the admin and a direct
        # ORM write cannot disagree about what a valid row is.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class OrganizationMembershipQuerySet(models.QuerySet):
    def active(self):
        """The memberships that grant tenant access. One definition, used everywhere.

        Phase 1's tenant rule is ``organization access == active
        OrganizationMembership`` in an active organization, so every endpoint
        that asks "may this caller reach this academy" comes through here — a
        suspended row or inactive organization is a record, not a key.
        """
        return self.filter(status=MembershipStatus.ACTIVE, organization__is_active=True)


class OrganizationMembership(models.Model):
    """A user's relationship with one academy: what they are allowed to be there.

    One row per user per organization, and the same user may hold rows in several
    organizations with different roles in each. That is why the foreign key runs
    this way round rather than ``User.organization`` — a field on ``User`` would
    hard-code "one user, one academy", which is not a safe assumption to build a
    multi-tenant platform on and would be expensive to undo later.
    """

    organization = models.ForeignKey(
        Organization,
        # Membership is a relationship rather than history: it holds no financial
        # or teaching record of its own in this phase, so it follows its
        # organization out. PROTECT would only be right once tenant-scoped records
        # hang off it, and organization deletion is an explicit later workflow.
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        User,
        # The same choice ParentLink makes for the same reason: the row describes
        # a relationship between two accounts and means nothing without both.
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    role = models.CharField(
        max_length=16,
        choices=OrganizationRole.choices,
        help_text=(
            "Authority inside this academy. Independent of User.role — a user may "
            "be 'teacher' here and 'student' globally."
        ),
    )
    status = models.CharField(
        max_length=16,
        choices=MembershipStatus.choices,
        default=MembershipStatus.ACTIVE,
        help_text="active grants tenant access; suspended keeps the row and revokes it.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrganizationMembershipQuerySet.as_manager()

    class Meta:
        # Creation order within an academy, which puts the owner first: their
        # membership is created in the same transaction as the organization.
        ordering = ["organization_id", "pk"]
        constraints = [
            # One row per user per organization. Phase 1 keeps no membership
            # history — reactivation flips ``status`` back rather than adding a
            # second row — so duplicates are prevented outright rather than
            # tolerated and filtered.
            models.UniqueConstraint(
                fields=["organization", "user"],
                name="unique_organization_membership",
                violation_error_message=(
                    "That user is already a member of this organization. Change "
                    "the existing membership instead of adding a second one."
                ),
            ),
            # "Exactly one initial owner, and no public API creates a second."
            # The serializers refuse to accept ``owner`` as an assignable role and
            # the permission layer refuses to touch the owner's row; this is the
            # backstop that also holds for the admin and a hand-written INSERT.
            # An ownership transfer will need to reckon with this constraint
            # deliberately, which is the intent — a transfer is a business
            # operation, not a role edit.
            models.UniqueConstraint(
                fields=["organization"],
                condition=Q(role=OrganizationRole.OWNER),
                name="unique_owner_per_organization",
                violation_error_message=(
                    "This organization already has an owner. Transferring "
                    "ownership is a separate operation, not a role change."
                ),
            ),
        ]

    @property
    def is_active(self) -> bool:
        return self.status == MembershipStatus.ACTIVE

    @property
    def can_manage_memberships(self) -> bool:
        """Owner and admin only, and only while the membership itself is active."""
        return self.is_active and self.role in MEMBERSHIP_MANAGER_ROLES

    def save(self, *args, **kwargs):
        # full_clean() covers the choice fields and, from Django 4.1, the two
        # constraints above — so a duplicate or a second owner is a
        # ValidationError with a readable message before it is an IntegrityError.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} @ {self.organization.slug} ({self.role})"


def active_membership(*, user, organization):
    """The caller's active membership in that organization, or ``None``.

    The one function that answers "is this user inside this tenant". Both the
    permission classes and the views that need the organization object go through
    it, so there is a single place where tenant access is decided — and it reads
    ``user`` and ``organization`` from the server's own request state, never from
    a client-supplied role or organization id.

    Accepts an ``Organization`` or a bare pk, because callers have the URL's id
    rather than the object. Returns ``None`` for an anonymous user, a non-member,
    and a suspended member alike: none of the three has tenant access, and the
    endpoint's answer should not distinguish between them.
    """
    if not getattr(user, "is_authenticated", False):
        return None
    organization_id = getattr(organization, "pk", organization)
    if organization_id is None:
        return None
    return (
        OrganizationMembership.objects.active()
        .filter(
            user=user,
            organization_id=organization_id,
            organization__is_active=True,
        )
        .select_related("organization")
        .first()
    )


def active_enrollment(*, user, organization):
    """The student's active enrollment in that organization, or ``None``.

    The one function that answers "is this student an active participant in
    this academy". Uses ``StudentEnrollment`` — the canonical academic
    participation relation established in B02 — rather than
    ``OrganizationMembership``, which answers a different question (authority).

    Accepts an ``Organization`` or a bare pk, because callers have the URL's id
    rather than the object. Returns ``None`` for an anonymous user, a non-enrolled
    student, and an inactive enrollment alike.
    """
    # Import here to avoid circular reference at module level; StudentEnrollment
    # is defined later in this same file.
    if not getattr(user, "is_authenticated", False):
        return None
    organization_id = getattr(organization, "pk", organization)
    if organization_id is None:
        return None
    return (
        StudentEnrollment.objects.active()
        .filter(
            user=user,
            organization_id=organization_id,
            organization__is_active=True,
        )
        .select_related("organization")
        .first()
    )


class EnrollmentStatus(models.TextChoices):
    """Status of a student's enrollment in an academy."""

    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class StudentEnrollmentQuerySet(models.QuerySet):
    def active(self):
        """The enrollments that represent active student participation in an academy.

        One definition, used everywhere. Mirrors ``OrganizationMembershipQuerySet.active()``
        for the enrollment relation: an inactive enrollment is a record, not participation.
        """
        return self.filter(status=EnrollmentStatus.ACTIVE)


class StudentEnrollment(models.Model):
    """A student's academic placement in one academy — *which programme they study here*.

    **Membership means what authority a person holds inside this academy;
    enrollment means which academic programme a student is placed in here.**

    This model answers the academy-specific academic questions:

    .. code-block:: text

        academy          → organization
        student          → user (must have role 'student')
        programme/track  → track (must belong to this organization)
        level/placement  → level (must belong to the enrollment's track)
        status           → active or inactive

    It is deliberately separate from ``OrganizationMembership``, which answers
    the authority question — a staff member who holds a membership is not a
    student, and a student who is enrolled is not automatically an admin. The
    two relations answer different questions and neither implies the other.

    Invariants enforced in ``clean()``:

    * ``user.role == 'student'``
    * ``organization == track.organization`` (when track is set)
    * ``level.track == track`` (when both are set)
    * A level cannot be set without a track
    * Enrollment does not cross academy boundaries
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="student_enrollments",
        help_text="The academy the student is enrolled in.",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="organization_enrollments",
        help_text="The student user.",
    )

    track = models.ForeignKey(
        "curriculum.Track",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_enrollments",
        help_text="The academic track the student is enrolled in.",
    )
    level = models.ForeignKey(
        "curriculum.Level",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_enrollments",
        help_text="The student's current level in the track.",
    )
    status = models.CharField(
        max_length=16,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ACTIVE,
        help_text="Whether the student is currently active in this academy.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StudentEnrollmentQuerySet.as_manager()

    class Meta:
        ordering = ["organization_id", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"],
                name="unique_student_enrollment",
                violation_error_message=(
                    "This student is already enrolled in this organization."
                ),
            )
        ]

    @property
    def is_active(self) -> bool:
        return self.status == EnrollmentStatus.ACTIVE

    def clean(self):
        from accounts.models import Role

        if getattr(self, "track_id", None):
            if self.track.organization_id != self.organization_id:
                raise ValidationError(
                    {
                        "track": ValidationError(
                            "The track belongs to a different organization.",
                            code="track_organization_mismatch",
                        )
                    }
                )
        if getattr(self, "level_id", None):
            if not getattr(self, "track_id", None):
                raise ValidationError(
                    {
                        "level": ValidationError(
                            "Cannot set a level without a track.",
                            code="level_without_track",
                        )
                    }
                )
            if self.level.track_id != self.track_id:
                raise ValidationError(
                    {
                        "level": ValidationError(
                            "The level belongs to a different track.",
                            code="level_track_mismatch",
                        )
                    }
                )
        if self.user_id and self.user.role != Role.STUDENT:
            raise ValidationError(
                {
                    "user": ValidationError(
                        "Only users with the 'student' role can be enrolled.",
                        code="invalid_student_role",
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} enrolled in {self.organization.slug} ({self.status})"

class InvitationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"


class OrganizationInvitation(models.Model):
    """An invitation to join an academy with a specific role.

    Phase B04's implementation of a real invitation lifecycle. Unlike the
    original behaviour of creating an active membership immediately, this
    model tracks the invitation's state, preventing the invited user from
    gaining access until they explicitly accept.

    It also permits inviting email addresses that do not yet correspond to
    a registered user, and matches them up when they sign up and accept.
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    email = models.EmailField(
        help_text="The email address invited. Used to match the user on acceptance.",
        db_index=True,
    )
    role = models.CharField(
        max_length=16,
        choices=OrganizationRole.choices,
        help_text="The role the user will hold upon acceptance.",
    )
    token_digest = models.CharField(
        max_length=128,
        unique=True,
        help_text="Hashed version of the token sent to the user.",
    )
    status = models.CharField(
        max_length=16,
        choices=InvitationStatus.choices,
        default=InvitationStatus.PENDING,
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=models.Q(status="pending"),
                name="unique_pending_invitation_per_email",
                violation_error_message="A pending invitation already exists for this email in this academy.",
            )
        ]

    def __str__(self):
        return f"{self.email} -> {self.organization.slug} ({self.status})"

    def is_valid(self) -> bool:
        """Returns True if the invitation is pending and not expired."""
        from django.utils import timezone

        return self.status == InvitationStatus.PENDING and self.expires_at > timezone.now()

    @classmethod
    def generate_token_and_digest(cls) -> tuple[str, str]:
        """Generate a random secure token and its SHA-256 digest."""
        import secrets
        import hashlib

        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        return token, digest
