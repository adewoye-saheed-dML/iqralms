"""API views for the organization app — the Phase 1 surface, nothing more.

Five endpoints, and what makes them a tenant boundary is that every one of them
answers "which academy" from the *URL plus the caller's own membership*, never
from the request body:

.. code-block:: text

    POST  /api/organizations/                                create a tenant, become its owner
    GET   /api/organizations/mine/                           the caller's own memberships
    GET   /api/organizations/{id}/                           read one academy — members only
    GET   /api/organizations/{id}/memberships/               the tenant's directory — owner/admin
    POST  /api/organizations/{id}/memberships/               add an existing user
    PATCH /api/organizations/{id}/memberships/{member_id}/   suspend, reactivate, re-role

Two mechanisms do the isolating, and both are needed. The permission classes
refuse a caller with no active membership in the organization named by the URL.
The querysets are then scoped to that same organization, so a membership id
belonging to another tenant is a **404** rather than a 403 — a 403 would confirm
the row exists, which is itself a cross-tenant leak.

``OrganizationDetailView`` goes one step further and reaches its object *through*
the caller's membership rather than through ``Organization.objects``. There is no
broad organization queryset in this module for a filter to be forgotten on.

Nothing here writes to ``accounts``, ``curriculum``, ``scheduling``, ``pricing``,
``assessment`` or ``payouts``. Phase 1 adds a tenant layer beside the existing
product; it does not reach into it.
"""

from functools import cached_property

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import OrganizationMembership, active_membership, OrganizationRole
from .permissions import (
    CanManageOrganizationMemberships,
    IsOrganizationMember,
    OwnerMembershipIsProtected,
)
from .serializers import (
    MyOrganizationMembershipSerializer,
    OrganizationInvitationAcceptSerializer,
    OrganizationInvitationCreateSerializer,
    OrganizationInvitationSerializer,
    OrganizationMembershipCreateSerializer,
    OrganizationMembershipSerializer,
    OrganizationMembershipUpdateSerializer,
    OrganizationSerializer,
    StudentDetailSerializer,
    StudentEnrollmentCreateSerializer,
    StudentEnrollmentUpdateSerializer,
    StudentListSerializer,
)


class OrganizationScopedMixin:
    """Resolves the URL's organization to the caller's own membership, once.

    The permission classes and the querysets both need the same answer to "is this
    caller inside this tenant, and as what", and asking twice is how the two
    eventually disagree. So it is resolved here, cached for the request, and read
    from ``view.caller_membership`` by everything else.

    ``None`` means no access — non-member, suspended member and anonymous caller
    alike — and the permission layer turns that into a refusal before any handler
    runs. Which is why ``organization`` may assume it is not None: by the time a
    handler executes, the caller has an active membership in this organization.
    """

    #: The URL kwarg naming the organization: ``pk`` on the organization's own
    #: detail route, ``organization_pk`` on the membership routes nested under it.
    organization_url_kwarg = "pk"

    @cached_property
    def caller_membership(self):
        return active_membership(
            user=self.request.user,
            organization=self.kwargs.get(self.organization_url_kwarg),
        )

    @property
    def organization(self):
        """The tenant this request is about, reached through the caller's membership."""
        return self.caller_membership.organization

    @property
    def organization_id(self):
        return self.kwargs[self.organization_url_kwarg]


# --- Organizations -----------------------------------------------------------


class OrganizationCreateView(generics.CreateAPIView):
    """POST /api/organizations/ — any authenticated user may found an academy.

    The creator becomes the organization's single ``owner`` in the same
    transaction, which the serializer guarantees; there is no request in which one
    happens without the other. Their ``accounts`` role is left exactly as it was:
    a student who founds an academy owns it and is still a student.

    No permission class beyond authentication. Who may create a tenant is a
    signup/billing question, and Phase 1 has neither — restricting it now would be
    inventing a product rule the spec explicitly leaves to later phases.
    """

    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=OrganizationSerializer,
                description=(
                    "Created, with the caller as its active owner. Read the "
                    "membership back from /api/organizations/mine/."
                ),
            ),
            400: OpenApiResponse(description="Taken slug, bad timezone, or no name."),
            401: OpenApiResponse(description="Not authenticated."),
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class MyOrganizationListView(generics.ListAPIView):
    """GET /api/organizations/mine/ — the academies the caller belongs to.

    One entry per active membership, each carrying the organization, the caller's
    role in it and the membership's status — enough for a client to know where the
    user can act and as what. A user in two academies sees two entries.

    Suspended memberships are absent, which is the same rule every other endpoint
    here applies: an active membership is what grants tenant access, and a
    suspended member reading the academy's name and timezone through this listing
    would be the one hole in it. Telling a suspended member *why* they lost access
    is a product decision for the onboarding phase (see learnings.md).
    """

    serializer_class = MyOrganizationMembershipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Scoped to the caller and nothing else. There is no query parameter that
        # could widen this to another user's memberships.
        return (
            OrganizationMembership.objects.active()
            .filter(user=self.request.user)
            .select_related("organization")
        )

    @extend_schema(
        responses={
            200: OpenApiResponse(response=MyOrganizationMembershipSerializer(many=True)),
            401: OpenApiResponse(description="Not authenticated."),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class OrganizationDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    """GET /api/organizations/{id}/ — one academy, for its members only.

    Knowing the id is not access. A non-member and a suspended member both get
    403, and the object is fetched through the caller's own membership rather than
    from ``Organization.objects``, so there is no queryset here that could return a
    tenant the caller does not belong to.

    Every active role may read the academy — owner, admin, staff and teacher. It is
    the organization's own name, slug and timezone; the things worth protecting
    inside a tenant are its members and, in later phases, its students, schedules
    and money.
    """

    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]

    def get_object(self):
        return self.organization

    @extend_schema(
        responses={
            200: OpenApiResponse(response=OrganizationSerializer),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an active member of this organization."),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# --- Memberships -------------------------------------------------------------


class OrganizationMembershipListView(
    OrganizationScopedMixin, generics.ListAPIView
):
    """/api/organizations/{id}/memberships/ — the tenant's directory, owner/admin only.

    GET lists the organization's memberships. Staff and teacher members are refused
    this: an ordinary member does not receive the academy's member directory in
    Phase 1.
    """

    permission_classes = [IsAuthenticated, CanManageOrganizationMemberships]
    organization_url_kwarg = "organization_pk"
    serializer_class = OrganizationMembershipSerializer

    def get_queryset(self):
        return OrganizationMembership.objects.filter(
            organization_id=self.organization_id
        ).select_related("user", "organization")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["organization"] = self.organization
        return context

    @extend_schema(
        responses={
            200: OpenApiResponse(response=OrganizationMembershipSerializer(many=True)),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class OrganizationMembershipDetailView(OrganizationScopedMixin, generics.UpdateAPIView):
    """PATCH /api/organizations/{id}/memberships/{member_id}/ — suspend, reactivate, re-role.

    The three membership-management operations the phase spec gives owner and
    admin, behind one partial update: ``status`` suspends or reactivates,
    ``role`` moves a member between ``admin``, ``staff`` and ``teacher``. The row
    is never deleted — a suspended membership is a record that someone was here.

    Two things this endpoint refuses. The owner's membership, to anyone including
    the owner, because demoting or suspending it is an ownership transfer rather
    than a role edit. And a membership belonging to another organization, which is
    a 404 from the queryset: the URL's tenant is the only one whose rows are
    visible here.

    PUT is not offered. A whole-object replace would have to accept
    ``organization`` and ``user``, and neither is editable — a membership pointing
    at a different tenant or a different person is a different membership.
    """

    serializer_class = OrganizationMembershipUpdateSerializer
    permission_classes = [
        IsAuthenticated,
        CanManageOrganizationMemberships,
        OwnerMembershipIsProtected,
    ]
    organization_url_kwarg = "organization_pk"
    http_method_names = ["patch", "head", "options"]

    def get_queryset(self):
        return OrganizationMembership.objects.filter(
            organization_id=self.organization_id
        ).select_related("user", "organization")

    @extend_schema(
        request=OrganizationMembershipUpdateSerializer,
        responses={
            200: OpenApiResponse(response=OrganizationMembershipSerializer),
            400: OpenApiResponse(
                description="Nothing to change, or a role that is not assignable."
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an owner or administrator here, or the target is the "
                    "organization's owner."
                )
            ),
            404: OpenApiResponse(
                description="No such membership in this organization."
            ),
        },
    )
    def patch(self, request, *args, **kwargs):
        membership = self.get_object()
        old_role = membership.role
        old_status = membership.status
        
        serializer = self.get_serializer(membership, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        
        from audit_logs.models import AuditAction
        metadata = {}
        action = None
        if old_role != membership.role:
            action = AuditAction.MEMBERSHIP_ROLE_CHANGED
            metadata["old_role"] = old_role
            metadata["new_role"] = membership.role
        elif old_status != membership.status:
            action = AuditAction.MEMBERSHIP_SUSPENDED if membership.status == "suspended" else AuditAction.MEMBERSHIP_REACTIVATED
            metadata["old_status"] = old_status
            metadata["new_status"] = membership.status
            
        from audit_logs.services import record_event
        if action:
            record_event(
                organization=self.organization,
                actor=request.user,
                action=action,
                target=membership,
                metadata=metadata,
            )
        
        body = OrganizationMembershipSerializer(
            membership, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_200_OK)


# --- Students ----------------------------------------------------------------


class StudentEnrollmentListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    """/api/organizations/<organization_pk>/students/ — the tenant's students.

    GET lists the organization's students; POST attaches an existing student.
    """

    permission_classes = [IsAuthenticated, CanManageOrganizationMemberships]
    organization_url_kwarg = "organization_pk"

    def get_queryset(self):
        from django.db.models import Q
        from .models import StudentEnrollment

        queryset = StudentEnrollment.objects.filter(
            organization_id=self.organization_id
        ).select_related("user", "organization")

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(user__username__icontains=search)
                | Q(user__email__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )

        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset

    def get_serializer_class(self):
        from .serializers import StudentListSerializer, StudentEnrollmentCreateSerializer

        if self.request.method == "POST":
            return StudentEnrollmentCreateSerializer
        return StudentListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["organization"] = self.organization
        return context

    @extend_schema(
        responses={
            200: StudentListSerializer(many=True),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=StudentEnrollmentCreateSerializer,
        responses={
            201: StudentListSerializer,
            400: OpenApiResponse(description="Unknown user, not a student, or already enrolled"),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
        }
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enrollment = serializer.save()

        from .serializers import StudentListSerializer

        body = StudentListSerializer(
            enrollment, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_201_CREATED)


class StudentEnrollmentDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    """/api/organizations/<organization_pk>/students/<id>/ — one student enrollment.

    GET retrieves the student's enrollment record.
    PATCH updates the enrollment status.
    """

    permission_classes = [IsAuthenticated, CanManageOrganizationMemberships]
    organization_url_kwarg = "organization_pk"
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        from .models import StudentEnrollment

        return StudentEnrollment.objects.filter(
            organization_id=self.organization_id
        ).select_related("user", "organization")

    def get_serializer_class(self):
        from .serializers import StudentDetailSerializer, StudentEnrollmentUpdateSerializer

        if self.request.method == "PATCH":
            return StudentEnrollmentUpdateSerializer
        return StudentDetailSerializer

    @extend_schema(
        responses={
            200: StudentDetailSerializer,
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
            404: OpenApiResponse(description="No such student in this organization."),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=StudentEnrollmentUpdateSerializer,
        responses={
            200: StudentDetailSerializer,
            400: OpenApiResponse(description="Nothing to change or invalid status"),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
            404: OpenApiResponse(description="No such student in this organization."),
        }
    )
    def patch(self, request, *args, **kwargs):
        enrollment = self.get_object()
        serializer = self.get_serializer(enrollment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        enrollment = serializer.save()

        from .serializers import StudentDetailSerializer

        body = StudentDetailSerializer(
            enrollment, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_200_OK)

from .models import OrganizationInvitation

class OrganizationInvitationListCreateView(OrganizationScopedMixin, generics.ListCreateAPIView):
    """/api/organizations/{id}/invitations/"""
    permission_classes = [IsAuthenticated, CanManageOrganizationMemberships]
    organization_url_kwarg = "organization_pk"

    def get_queryset(self):
        return OrganizationInvitation.objects.filter(
            organization_id=self.organization_id
        ).order_by("-created_at")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return OrganizationInvitationCreateSerializer
        return OrganizationInvitationSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["organization"] = self.organization
        return context

    def perform_create(self, serializer):
        invitation = serializer.save()
        # notify teacher invitation
        if invitation.role == OrganizationRole.TEACHER:
            from notifications.services import notify_teacher_invitation
            notify_teacher_invitation(invitation)
        return invitation

    @extend_schema(
        responses={
            200: OrganizationInvitationSerializer(many=True),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=OrganizationInvitationCreateSerializer,
        responses={
            201: OrganizationInvitationSerializer,
            400: OpenApiResponse(description="Invalid invitation data."),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
        }
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = self.perform_create(serializer)

        data = OrganizationInvitationSerializer(invitation).data
        return Response(data, status=status.HTTP_201_CREATED)


class OrganizationInvitationAcceptView(generics.GenericAPIView):
    """POST /api/organizations/{id}/invitations/accept/"""
    permission_classes = [IsAuthenticated]
    serializer_class = OrganizationInvitationAcceptSerializer
    
    def get_organization(self):
        from django.shortcuts import get_object_or_404
        from .models import Organization
        return get_object_or_404(Organization, pk=self.kwargs["organization_pk"])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["organization"] = self.get_organization()
        return context

    @extend_schema(
        request=OrganizationInvitationAcceptSerializer,
        responses={200: OpenApiResponse(response=OrganizationMembershipSerializer)},
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        
        # Return the resulting membership
        data = OrganizationMembershipSerializer(membership).data
        return Response(data, status=status.HTTP_200_OK)
