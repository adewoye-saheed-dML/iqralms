"""Serializers for the accounts app.

Datetimes are stored in UTC and rendered in the requesting user's stored
timezone here at the serializer layer, per CLAUDE.md.

**Global shapes and organization-scoped shapes.** Everything down to
``ParentLinkCreateSerializer`` describes a *global* account: registration, the
caller's own profile, a child as their parent sees them. None of it carries
organization-owned data, which is why SaaS Phase 2 left those endpoints alone.

Below them are the organization-scoped shapes, and they follow one rule that the
global ones never have to think about: **the academy comes from the view, never
from the request body.** ``self.context["organization"]`` is the tenant the view
already verified the caller into, so a membership or a user named in a payload can
only be resolved *within* it — there is no field here a client can set to reach
another academy's rows.
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone as dj_timezone
from rest_framework import serializers

from organizations.models import OrganizationMembership

from .models import (
    OrganizationTeacherConfiguration,
    ParentLink,
    Role,
    TeacherProfile,
    User,
)
from .utils import normalize_signup_code, to_user_timezone

#: Roles a member of the public may sign themselves up as. 'lead' is excluded
#: deliberately: it is the single academy-owner account and is created through
#: createsuperuser / the admin, not a public endpoint.
SELF_REGISTERABLE_ROLES = (Role.SUB.value, Role.STUDENT.value, Role.PARENT.value)


def as_drf_error(exc):
    """Re-raise a model ``ValidationError`` as a DRF one, so it lands as a 400.

    The local copy every app in this repository keeps: four lines, and the apps are
    otherwise independent.
    """
    return serializers.ValidationError(
        getattr(exc, "message_dict", None) or {"detail": exc.messages}
    )


class TeacherProfileSerializer(serializers.ModelSerializer):
    """Read-only in this phase; profiles are created via the admin."""

    class Meta:
        model = TeacherProfile
        fields = ["bio", "max_weekly_hours", "hourly_payout_rate", "is_lead", "approved"]
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    """The requesting user's own profile."""

    is_fully_active = serializers.BooleanField(read_only=True)
    teacher_profile = TeacherProfileSerializer(read_only=True)
    date_joined_local = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "timezone",
            "date_of_birth",
            "is_minor",
            "is_fully_active",
            "signup_code",
            "teacher_profile",
            "date_joined",
            "date_joined_local",
        ]
        read_only_fields = [
            "id",
            "role",
            "is_minor",
            "is_fully_active",
            "signup_code",
            "teacher_profile",
            "date_joined",
        ]

    def get_date_joined_local(self, obj) -> str:
        """date_joined rendered in the user's own timezone (stored as UTC)."""
        local = to_user_timezone(obj.date_joined, obj.timezone)
        return local.isoformat() if local else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Only teachers carry a profile; drop the null for everyone else.
        if not instance.is_teacher:
            data.pop("teacher_profile", None)
        # The signup code only means something for students (a parent types it
        # to link). Don't advertise a code nobody will ever use.
        if instance.role != Role.STUDENT:
            data.pop("signup_code", None)
        return data


class LinkedStudentSerializer(serializers.ModelSerializer):
    """A child as seen by their linked parent — no signup_code, no PII sprawl."""

    is_fully_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "timezone",
            "date_of_birth",
            "is_minor",
            "is_fully_active",
        ]
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    role = serializers.ChoiceField(choices=SELF_REGISTERABLE_ROLES)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "role",
            "timezone",
            "date_of_birth",
        ]
        extra_kwargs = {
            "email": {"required": True, "allow_blank": False},
            "timezone": {"required": True, "allow_blank": False},
        }

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_date_of_birth(self, value):
        if value and value > dj_timezone.localdate():
            raise serializers.ValidationError("Date of birth cannot be in the future.")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        # is_minor is a signup-time snapshot, never client-supplied.
        validated_data["is_minor"] = User.minor_from_date_of_birth(
            validated_data.get("date_of_birth")
        )
        user = User(**validated_data)
        user.set_password(password)
        try:
            # signup_code is generated in User.save(), so it is blank here.
            user.full_clean(exclude=["signup_code"])
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        user.save()
        return user


class ParentLinkCreateSerializer(serializers.Serializer):
    """A parent links themselves to a student using the student's signup code.

    The view resolves ``student_code`` to a user (404 when no student holds that
    code) and passes it to ``save(student=...)``.
    """

    student_code = serializers.CharField(write_only=True)
    student = LinkedStudentSerializer(read_only=True)

    def validate_student_code(self, value):
        code = normalize_signup_code(value)
        if not code:
            raise serializers.ValidationError("Enter the student's signup code.")
        return code

    def create(self, validated_data):
        parent = self.context["request"].user
        student = validated_data["student"]
        link = ParentLink(parent=parent, student=student)
        try:
            # ParentLink.save() runs full_clean(), which covers both the role
            # rules and the unique (parent, student) constraint.
            link.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return link


# --- Organization-scoped shapes ----------------------------------------------


class OrganizationTeacherConfigurationSerializer(serializers.ModelSerializer):
    """One academy's terms for one teacher, as an owner or admin reads them.

    ``user`` and ``organization`` travel as ids alongside the membership because a
    client reading a list of teaching terms needs to know whose they are without a
    second request, and ``username`` because an id does not identify a person to the
    human deciding whether to approve them. Nothing else from the account: this is a
    teaching-terms record, not a user directory.

    Entirely read-only. Writes go through the create and update shapes below, so
    there is no path here that could move a configuration to another membership.
    """

    user = serializers.PrimaryKeyRelatedField(source="membership.user", read_only=True)
    username = serializers.CharField(source="membership.user.username", read_only=True)
    organization = serializers.PrimaryKeyRelatedField(
        source="membership.organization", read_only=True
    )

    class Meta:
        model = OrganizationTeacherConfiguration
        fields = [
            "id",
            "membership",
            "user",
            "username",
            "organization",
            "max_weekly_hours",
            "hourly_payout_rate",
            "approved",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class OrganizationTeacherConfigurationCreateSerializer(serializers.Serializer):
    """Giving an existing member of this academy their teaching terms.

    The caller names a ``user``, not a membership — the same shape
    ``OrganizationMembershipCreateSerializer`` uses, and for the same reason: the
    membership is then looked up *inside* the view's verified organization, so a
    membership id belonging to another academy is not something a request can even
    express. The academy is never in the payload.

    ``approved`` may be sent at creation. Unlike the global ``TeacherProfile``,
    which the admin approves after the fact, an academy adding a teacher it has
    already vetted should not have to make two calls; the default is still False.
    """

    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    max_weekly_hours = serializers.IntegerField(min_value=1)
    hourly_payout_rate = serializers.DecimalField(
        max_digits=8, decimal_places=2, required=False, allow_null=True, default=None
    )
    approved = serializers.BooleanField(required=False, default=False)

    def validate_user(self, user):
        organization = self.context["organization"]
        membership = OrganizationMembership.objects.filter(
            organization=organization, user=user
        ).first()
        if membership is None:
            # A statement about *this* academy, which the caller already
            # administers — it says nothing about any other tenant the user may
            # belong to.
            raise serializers.ValidationError(
                "That user is not a member of this organization. Add the "
                "membership first."
            )
        if hasattr(membership, "teacher_configuration"):
            # A friendlier 400 than the one-to-one's, and on the field the client
            # actually sent. The database constraint still holds underneath.
            raise serializers.ValidationError(
                "That member already has teaching terms here. Change the existing "
                "ones instead of adding a second set."
            )
        if not user.is_teacher:
            # The model enforces this too, keyed on 'membership'; repeating it here
            # puts the error on the field the caller sent. Preserving the account
            # domain's rule rather than letting an academy invent a teaching
            # identity it does not support (see accounts/models.py).
            raise serializers.ValidationError(
                "Only an account whose role is 'lead' or 'sub' can be given "
                f"teaching terms (got '{user.role}')."
            )
        return user

    def create(self, validated_data):
        membership = OrganizationMembership.objects.get(
            organization=self.context["organization"], user=validated_data["user"]
        )
        configuration = OrganizationTeacherConfiguration(
            membership=membership,
            max_weekly_hours=validated_data["max_weekly_hours"],
            hourly_payout_rate=validated_data["hourly_payout_rate"],
            approved=validated_data["approved"],
        )
        try:
            configuration.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return configuration


class OrganizationTeacherConfigurationUpdateSerializer(serializers.Serializer):
    """Changing what this academy asks of, and pays, one of its teachers.

    All three fields optional, so one shape covers approving a teacher, re-capping
    their week and re-rating their hour. ``membership`` is absent and not editable:
    moving these terms to a different person or a different academy is not a change
    to a relationship, it is a different relationship.
    """

    max_weekly_hours = serializers.IntegerField(min_value=1, required=False)
    hourly_payout_rate = serializers.DecimalField(
        max_digits=8, decimal_places=2, required=False, allow_null=True
    )
    approved = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Send 'max_weekly_hours', 'hourly_payout_rate' or 'approved' — "
                "there is nothing else to change."
            )
        return attrs

    def update(self, configuration, validated_data):
        for field in ("max_weekly_hours", "hourly_payout_rate", "approved"):
            if field in validated_data:
                setattr(configuration, field, validated_data[field])
        try:
            configuration.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return configuration
