"""Serializers for the organization API.

Three things to know here.

**Organization creation also creates its owner.** ``OrganizationSerializer.create``
writes the organization and the creator's ``owner`` membership inside one
``transaction.atomic()``, so a tenant with no owner cannot exist — not even
briefly, and not if the membership write is refused. The caller does not supply
the owner and cannot: it is ``request.user``, taken from the authenticated
request.

**``owner`` is not an assignable role.** Every membership write here offers
``admin``/``staff``/``teacher`` and nothing else, so no request body can create a
second owner or promote someone into the seat. That is the serializer's half of
the rule; the permission layer refuses to touch the owner's existing row, and
``OrganizationMembership``'s partial unique constraint is the database's backstop.
Three layers, because "one owner" is the invariant the whole ownership model rests
on.

**``User.role`` is never written.** Creating an academy does not make its creator a
lead teacher, and a ``teacher`` membership does not make anyone a ``sub``. The two
role systems answer different questions and Phase 1 keeps them apart — a student
who founds an academy is its owner and stays a student.

Authorization is not decided in this module. Who may call these endpoints is the
permission classes' answer, and the tenant a membership lands in comes from the
view's URL rather than from any field below.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from accounts.models import User

from .models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
)

#: The organization roles a membership request may ask for.
ASSIGNABLE_ORGANIZATION_ROLES = (
    OrganizationRole.ADMIN.value,
    OrganizationRole.STAFF.value,
    OrganizationRole.TEACHER.value,
    OrganizationRole.PARENT.value,
    OrganizationRole.STUDENT.value,
)

#: The organization roles an invitation may ask for. owner and staff are excluded.
INVITABLE_ORGANIZATION_ROLES = (
    OrganizationRole.ADMIN.value,
    OrganizationRole.TEACHER.value,
    OrganizationRole.PARENT.value,
    OrganizationRole.STUDENT.value,
)


def as_drf_error(exc):
    """Re-raise a model ``ValidationError`` as a DRF one, so it lands as a 400.

    The local copy ``scheduling``, ``pricing``, ``assessment`` and ``payouts`` each
    keep: four lines, and the apps are otherwise independent.
    """
    return serializers.ValidationError(
        getattr(exc, "message_dict", None) or {"detail": exc.messages}
    )


class OrganizationSerializer(serializers.ModelSerializer):
    """An academy — the read shape, and the one that creates a tenant.

    ``is_active`` is read-only: disabling an academy is a later phase's workflow
    with its own consequences for the people inside it, not a field a creation
    request gets to set. ``timezone`` is validated by the model's own IANA
    validator, which the ``ModelSerializer`` picks up, so the API and a direct ORM
    write reject the same strings.
    """

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "slug",
            "timezone",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_active", "created_at", "updated_at"]

    def create(self, validated_data):
        creator = self.context["request"].user
        try:
            # One transaction, all-or-nothing: an organization whose owner
            # membership was refused must not remain committed, because a tenant
            # nobody can administer is worse than a failed request. The spec is
            # explicit, and the same lesson learnings.md records from the Phase 7
            # rubric write and the Phase 8 payroll run.
            with transaction.atomic():
                organization = Organization.objects.create(**validated_data)
                OrganizationMembership.objects.create(
                    organization=organization,
                    user=creator,
                    role=OrganizationRole.OWNER,
                    status=MembershipStatus.ACTIVE,
                )
            # Nothing above touches creator.role. That is the point of Phase 1:
            # organization authority and account role are separate concepts.
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return organization


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    """One membership, as an owner or admin reads it in the tenant's directory.

    ``username`` travels with the user id because a membership list is read by a
    human deciding who to suspend or promote, and an id alone does not identify a
    person. No email, no signup code, no account detail beyond the name: this is a
    membership record, not a user directory, and ``accounts`` owns the latter.
    """

    username = serializers.CharField(source="user.username", read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = [
            "id",
            "organization",
            "user",
            "username",
            "role",
            "role_display",
            "status",
            "status_display",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MyOrganizationMembershipSerializer(serializers.ModelSerializer):
    """The caller's own membership, with the academy it is in — the ``/mine/`` shape.

    Membership id, organization, organization role and status: enough for a client
    to know which academies the user is in and what they may do in each, which is
    what the spec asks of this endpoint. Another user's membership never appears
    here; the view scopes the queryset to ``request.user``.
    """

    organization = OrganizationSerializer(read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = [
            "id",
            "organization",
            "role",
            "role_display",
            "status",
            "status_display",
            "created_at",
        ]
        read_only_fields = fields


class OrganizationMembershipUpdateSerializer(serializers.Serializer):
    """Suspending, reactivating, or changing what a member may do here.

    Both fields are optional, so this is the one shape behind three operations —
    suspend, reactivate, re-role. Neither ``organization`` nor ``user`` is
    editable: moving a membership between tenants or between people is not a
    change to a relationship, it is a different relationship.

    ``role`` offers the assignable subset, so a PATCH cannot promote anyone to
    ``owner``; the owner's own row is refused earlier still, by
    ``permissions.OwnerMembershipIsProtected``.
    """

    role = serializers.ChoiceField(
        choices=ASSIGNABLE_ORGANIZATION_ROLES, required=False
    )
    status = serializers.ChoiceField(choices=MembershipStatus.choices, required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Send 'role', 'status', or both — there is nothing else to change."
            )
        return attrs

    def update(self, membership, validated_data):
        for field in ("role", "status"):
            if field in validated_data:
                setattr(membership, field, validated_data[field])
        try:
            membership.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return membership


class StudentListSerializer(serializers.ModelSerializer):
    """A student's enrollment, as seen in the academy's list."""

    user_id = serializers.IntegerField(source="user.id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.CharField(source="user.email", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    date_of_birth = serializers.DateField(source="user.date_of_birth", read_only=True)
    is_minor = serializers.BooleanField(source="user.is_minor", read_only=True)
    enrollment_status = serializers.CharField(source="status", read_only=True)
    track_id = serializers.IntegerField(source="track.id", read_only=True, allow_null=True)
    level_id = serializers.IntegerField(source="level.id", read_only=True, allow_null=True)

    class Meta:
        from .models import StudentEnrollment

        model = StudentEnrollment
        fields = [
            "id",
            "user_id",
            "username",
            "email",
            "first_name",
            "last_name",
            "date_of_birth",
            "is_minor",

            "enrollment_status",
            "track_id",
            "level_id",

            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class StudentDetailSerializer(StudentListSerializer):
    """A student's enrollment, as seen in the academy's detail view."""
    pass


class StudentEnrollmentCreateSerializer(serializers.Serializer):
    """Enrolling an existing student user into an academy."""


    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    from curriculum.models import Track, Level
    track_id = serializers.PrimaryKeyRelatedField(
        queryset=Track.objects.all(), source="track", required=False, allow_null=True
    )
    level_id = serializers.PrimaryKeyRelatedField(
        queryset=Level.objects.all(), source="level", required=False, allow_null=True
    )


    def validate_user(self, user):
        from accounts.models import Role
        from .models import StudentEnrollment

        organization = self.context["organization"]

        if user.role != Role.STUDENT:
            raise serializers.ValidationError("Only users with role 'student' can be enrolled.")

        if StudentEnrollment.objects.filter(organization=organization, user=user).exists():
            raise serializers.ValidationError("This student is already enrolled in this organization.")

        return user

    def create(self, validated_data):
        from .models import StudentEnrollment, EnrollmentStatus
        

        enrollment = StudentEnrollment(
            organization=self.context["organization"],
            user=validated_data["user"],
            track=validated_data.get("track"),
            level=validated_data.get("level"),
            status=EnrollmentStatus.ACTIVE,
        )

        try:
            enrollment.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return enrollment


class StudentEnrollmentUpdateSerializer(serializers.Serializer):
    """Updating a student's enrollment status."""


    from .models import EnrollmentStatus
    from curriculum.models import Track, Level
    status = serializers.ChoiceField(choices=EnrollmentStatus.choices, required=False)
    track_id = serializers.PrimaryKeyRelatedField(
        queryset=Track.objects.all(), source="track", required=False, allow_null=True
    )
    level_id = serializers.PrimaryKeyRelatedField(
        queryset=Level.objects.all(), source="level", required=False, allow_null=True
    )


    def update(self, enrollment, validated_data):

        if "status" in validated_data:
            enrollment.status = validated_data["status"]
        if "track" in validated_data:
            enrollment.track = validated_data["track"]
        if "level" in validated_data:
            enrollment.level = validated_data["level"]

        try:
            enrollment.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return enrollment

from .models import OrganizationInvitation, InvitationStatus

class OrganizationInvitationSerializer(serializers.ModelSerializer):
    email_delivery_status = serializers.CharField(read_only=True)

    class Meta:
        model = OrganizationInvitation
        fields = [
            "id",
            "email",
            "role",
            "status",
            "expires_at",
            "created_at",
            "email_delivery_status",
        ]
        read_only_fields = fields


class OrganizationInvitationPreviewSerializer(serializers.Serializer):
    """Safe public representation of an invitation for the acceptance screen."""
    organization_id = serializers.IntegerField(source="organization.id", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    role = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)


class OrganizationInvitationCreateSerializer(serializers.Serializer):
    """Creates a pending invitation for an email address."""
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=INVITABLE_ORGANIZATION_ROLES)

    def validate_email(self, email):
        organization = self.context["organization"]
        norm_email = email.strip().lower()

        # If user with email exists, are they already a member or suspended?
        user = User.objects.filter(email__iexact=norm_email).first()
        if user:
            existing_membership = OrganizationMembership.objects.filter(
                organization=organization, user=user
            ).first()
            if existing_membership:
                if existing_membership.status == MembershipStatus.SUSPENDED:
                    raise serializers.ValidationError(
                        "A user with this email has a suspended membership in this organization."
                    )
                raise serializers.ValidationError(
                    "A user with this email is already a member of this organization."
                )

        # Check if pending invitation exists
        if OrganizationInvitation.objects.filter(
            organization=organization, email__iexact=norm_email, status=InvitationStatus.PENDING
        ).exists():
            raise serializers.ValidationError(
                "A pending invitation already exists for this email."
            )
        return norm_email

    def save(self):
        email = self.validated_data["email"].strip().lower()
        role = self.validated_data["role"]
        organization = self.context["organization"]
        from django.utils import timezone
        import datetime

        token, digest = OrganizationInvitation.generate_token_and_digest()

        invitation = OrganizationInvitation.objects.create(
            organization=organization,
            email=email,
            role=role,
            token_digest=digest,
            expires_at=timezone.now() + datetime.timedelta(days=7),
            status=InvitationStatus.PENDING,
        )

        # We attach the raw token to the instance for the view to use
        invitation.raw_token = token
        return invitation


class OrganizationInvitationAcceptSerializer(serializers.Serializer):
    """Accepts an invitation using its token."""
    token = serializers.CharField()

    def validate(self, attrs):
        token = attrs["token"]
        organization = self.context["organization"]
        user = self.context["request"].user

        import hashlib
        digest = hashlib.sha256(token.encode()).hexdigest()

        invitation = OrganizationInvitation.objects.filter(
            organization=organization,
            token_digest=digest
        ).first()

        if not invitation:
            raise serializers.ValidationError({"token": "Invalid invitation token."})

        if invitation.status != InvitationStatus.PENDING:
            raise serializers.ValidationError({"token": "This invitation is no longer pending."})

        if not invitation.is_valid():
            invitation.status = InvitationStatus.EXPIRED
            invitation.save(update_fields=["status"])
            raise serializers.ValidationError({"token": "This invitation has expired."})

        if user.email.lower() != invitation.email.lower():
            raise serializers.ValidationError({"token": "This invitation was sent to a different email address."})

        if invitation.role == OrganizationRole.OWNER:
            raise serializers.ValidationError(
                {"detail": "Owner cannot be created through an invitation."}
            )

        if invitation.role == OrganizationRole.STAFF:
            raise serializers.ValidationError(
                {"detail": "Staff cannot be created through an invitation."}
            )

        # Role compatibility check
        from accounts.models import Role
        if invitation.role == OrganizationRole.TEACHER:
            if user.role not in {Role.LEAD, Role.SUB}:
                raise serializers.ValidationError(
                    {"detail": "Your account role is not compatible with a teacher invitation."}
                )
        elif invitation.role == OrganizationRole.PARENT:
            if user.role != Role.PARENT:
                raise serializers.ValidationError(
                    {"detail": "Your account role is not compatible with a parent invitation."}
                )
        elif invitation.role == OrganizationRole.STUDENT:
            if user.role != Role.STUDENT:
                raise serializers.ValidationError(
                    {"detail": "Your account role is not compatible with a student invitation."}
                )

        existing_membership = OrganizationMembership.objects.filter(
            organization=organization, user=user
        ).first()
        if existing_membership:
            if existing_membership.status == MembershipStatus.SUSPENDED:
                raise serializers.ValidationError(
                    {"detail": "You are already a member of this organization (suspended)."}
                )
            raise serializers.ValidationError(
                {"detail": "You are already a member of this organization."}
            )

        attrs["invitation"] = invitation
        return attrs

    def save(self):
        invitation = self.validated_data["invitation"]
        user = self.context["request"].user
        from django.db import transaction
        from django.utils import timezone
        from audit_logs.models import AuditAction
        from audit_logs.services import record_event

        with transaction.atomic():
            # 1. Create the active membership
            membership = OrganizationMembership.objects.create(
                organization=invitation.organization,
                user=user,
                role=invitation.role,
                status=MembershipStatus.ACTIVE,
            )

            # 2. Mark invitation as accepted
            invitation.status = InvitationStatus.ACCEPTED
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["status", "accepted_at"])

            # 3. Record audit log
            record_event(
                organization=invitation.organization,
                actor=user,
                action=AuditAction.INVITATION_ACCEPTED,
                target=invitation,
                metadata={
                    "email": invitation.email,
                    "role": invitation.role,
                    "membership_id": membership.id,
                },
            )

        return membership
