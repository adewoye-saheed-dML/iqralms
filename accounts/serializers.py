"""Serializers for the accounts app.

Datetimes are stored in UTC and rendered in the requesting user's stored
timezone here at the serializer layer, per CLAUDE.md.
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone as dj_timezone
from rest_framework import serializers

from .models import ParentLink, Role, TeacherProfile, User
from .utils import normalize_signup_code, to_user_timezone

#: Roles a member of the public may sign themselves up as. 'lead' is excluded
#: deliberately: it is the single academy-owner account and is created through
#: createsuperuser / the admin, not a public endpoint.
SELF_REGISTERABLE_ROLES = (Role.SUB.value, Role.STUDENT.value, Role.PARENT.value)


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
            raise serializers.ValidationError(
                getattr(exc, "message_dict", None) or {"detail": exc.messages}
            ) from exc
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
            raise serializers.ValidationError(
                getattr(exc, "message_dict", None) or {"detail": exc.messages}
            ) from exc
        return link
