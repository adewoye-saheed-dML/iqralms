"""API views for the accounts app.

Two groups, and the split is the point of SaaS Phase 2:

.. code-block:: text

    global account endpoints
    POST /api/auth/register/                                   create an account
    GET  /api/accounts/me/                                     the caller's own profile
    GET  /api/accounts/my-children/                            the caller's linked children
    POST /api/accounts/parent-links/                           link to a child by signup code

    organization-scoped account endpoints
    GET   /api/accounts/organizations/{id}/children/                       children in this academy
    GET   /api/accounts/organizations/{id}/teacher-configurations/         this academy's teaching terms
    POST  /api/accounts/organizations/{id}/teacher-configurations/         set a teacher's terms here
    GET   /api/accounts/organizations/{id}/teacher-configurations/{pk}/    read one
    PATCH /api/accounts/organizations/{id}/teacher-configurations/{pk}/    approve, re-cap, re-rate

The four global ones were classified and deliberately left alone. Registration
creates a ``User`` and no membership, so a public signup cannot land inside someone
else's academy. ``/me/`` is a global identity endpoint and does not list the
caller's academies — ``GET /api/organizations/mine/`` already answers that, and a
second shape for the same fact is a second thing to keep true. ``/my-children/``
and ``/parent-links/`` carry only global account fields, which is what lets them
stay account-level: a ``ParentLink`` is a family relationship, not an academy's
record of a student.

The organization-scoped ones follow one shape, which is the phase's security model:

.. code-block:: text

    organization in the URL
            |
            v
    active membership  (organizations.active_membership, via OrganizationScopedMixin)
            |
            v
    queryset filtered to that organization

Both halves are load-bearing. The permission classes are the organization app's own
— no second implementation — and every queryset is scoped to the URL's academy, so a
configuration id belonging to another tenant is a **404** rather than a 403: a 403
would confirm the row exists. Nothing here reads an organization, a role or a user
from the request body as a statement about who the caller is.

Nothing in this module touches curriculum, scheduling, pricing, assessment or
payouts.
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from organizations.permissions import (
    CanManageOrganizationMemberships,
    IsOrganizationMember,
)
from organizations.views import OrganizationScopedMixin

from .models import OrganizationTeacherConfiguration, Role, User
from .serializers import (
    LinkedStudentSerializer,
    OrganizationTeacherConfigurationCreateSerializer,
    OrganizationTeacherConfigurationSerializer,
    OrganizationTeacherConfigurationUpdateSerializer,
    ParentLinkCreateSerializer,
    RegisterSerializer,
    UserSerializer,
)
from .tenancy import children_in_organization

#: Returned to a minor student who has no parent linked yet.
STATUS_PENDING_PARENT_LINK = "pending_parent_link"
STATUS_ACTIVE = "active"


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register/ — create a user.

    A minor student comes back with status ``pending_parent_link``: the account
    exists and can log in, but is not complete until a parent links to it.
    """

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                description=(
                    "Created. Body carries the user, an account status of "
                    "'active' or 'pending_parent_link', and a human-readable "
                    "detail message."
                )
            )
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        pending = not user.is_fully_active
        if pending:
            detail = (
                "Account created. A parent must link to this student using "
                f"signup code {user.signup_code} before it can be used."
            )
        else:
            detail = "Account created."

        body = {
            "user": UserSerializer(user, context=self.get_serializer_context()).data,
            "status": STATUS_PENDING_PARENT_LINK if pending else STATUS_ACTIVE,
            "detail": detail,
        }
        return Response(body, status=status.HTTP_201_CREATED)


class MeView(generics.RetrieveAPIView):
    """GET /api/accounts/me/ — the logged-in user's own profile."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        # select_related so a teacher's profile is one query, not two.
        return (
            User.objects.select_related("teacher_profile")
            .get(pk=self.request.user.pk)
        )


class MyChildrenView(generics.ListAPIView):
    """GET /api/accounts/my-children/ — students linked to the calling parent."""

    serializer_class = LinkedStudentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role != Role.PARENT:
            raise PermissionDenied("Only a parent account has linked children.")
        return User.objects.filter(parent_links__parent=user).order_by("username")


class ParentLinkCreateView(generics.CreateAPIView):
    """POST /api/accounts/parent-links/ — parent links to a student by code."""

    serializer_class = ParentLinkCreateSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        if self.request.user.role != Role.PARENT:
            raise PermissionDenied("Only a parent account can create a parent link.")

        code = serializer.validated_data["student_code"]
        try:
            student = User.objects.get(signup_code=code, role=Role.STUDENT)
        except User.DoesNotExist:
            # Deliberately the same response whether the code is unknown or
            # belongs to a non-student, so this cannot be used to probe accounts.
            raise NotFound("No student found for that signup code.")

        serializer.save(student=student)


# --- Organization-scoped account endpoints -----------------------------------


class OrganizationChildrenView(OrganizationScopedMixin, generics.ListAPIView):
    """GET /api/accounts/organizations/{id}/children/ — the caller's children *here*.

    The endpoint SaaS Phase 2 exists for. ``/my-children/`` answers "who are this
    parent's children", globally and correctly; this one answers "which of them is a
    student of *this* academy", and the difference between the two answers is the
    tenant boundary.

    Both sides must be active members: a parent whose own membership is suspended
    gets an empty list, and a linked child the academy has not admitted (or has
    suspended) is absent from it. So a real, global parent-child relationship cannot
    be used to make Academy A show a student who belongs to Academy B —
    ``tenancy.children_in_organization()`` is the one place that rule lives.

    A non-member is refused by ``IsOrganizationMember`` before the queryset runs,
    and a caller who is not a parent account gets the same 403
    ``/my-children/`` gives them. The fields are the same global ones too: no
    signup code, and nothing the academy owns.
    """

    serializer_class = LinkedStudentSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]
    organization_url_kwarg = "organization_pk"

    def get_queryset(self):
        if self.request.user.role != Role.PARENT:
            raise PermissionDenied("Only a parent account has linked children.")
        return children_in_organization(
            parent=self.request.user, organization=self.organization
        )

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=LinkedStudentSerializer(many=True),
                description=(
                    "The caller's linked children who are active members of this "
                    "organization. Empty when none are."
                ),
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member of this organization, or not a parent "
                    "account."
                )
            ),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class TeacherConfigurationScopedMixin(OrganizationScopedMixin):
    """The academy's teaching terms, and nothing else's.

    One queryset for both views below, filtered through
    ``membership__organization`` — the configuration's only route to a tenant, since
    it holds no organization field of its own. A configuration belonging to another
    academy is therefore absent rather than forbidden, which is what makes the
    detail route answer 404 for it.
    """

    organization_url_kwarg = "organization_pk"

    def get_queryset(self):
        return OrganizationTeacherConfiguration.objects.filter(
            membership__organization_id=self.organization_id
        ).select_related("membership__user", "membership__organization")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # The tenant, from the URL and the caller's verified membership — never
        # from input.
        context["organization"] = self.organization
        return context


class OrganizationTeacherConfigurationListCreateView(
    TeacherConfigurationScopedMixin, generics.ListCreateAPIView
):
    """/api/accounts/organizations/{id}/teacher-configurations/ — owner/admin only.

    GET lists this academy's teaching terms; POST gives an existing member theirs.
    Staff and teacher members are refused both, the same narrower default the
    membership directory takes — what an academy pays its teachers is not something
    an ordinary member reads, and a narrow rule can be widened safely later.

    POST names a ``user``, and the membership is resolved inside this academy, so
    the request cannot reach a membership it does not administer. Only a ``lead`` or
    ``sub`` account may be given teaching terms: an organization role is authority,
    not a teaching identity, and Phase 2 preserves the account domain's rule about
    who can teach.
    """

    permission_classes = [IsAuthenticated, CanManageOrganizationMemberships]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return OrganizationTeacherConfigurationCreateSerializer
        return OrganizationTeacherConfigurationSerializer

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=OrganizationTeacherConfigurationSerializer(many=True)
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=OrganizationTeacherConfigurationCreateSerializer,
        responses={
            201: OpenApiResponse(response=OrganizationTeacherConfigurationSerializer),
            400: OpenApiResponse(
                description=(
                    "Not a member of this organization, already configured here, "
                    "or an account that cannot teach."
                )
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        configuration = serializer.save()
        body = OrganizationTeacherConfigurationSerializer(
            configuration, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_201_CREATED)


class OrganizationTeacherConfigurationDetailView(
    TeacherConfigurationScopedMixin, generics.RetrieveUpdateAPIView
):
    """GET/PATCH one set of teaching terms — approve, re-cap the week, re-rate the hour.

    The endpoint that proves the phase: PATCHing a teacher's terms here changes what
    *this* academy asks of them and nothing about any other academy they work for,
    because the row being written belongs to one membership. A configuration id from
    another tenant is a 404 from the scoped queryset.

    PUT is not offered. A whole-object replace would have to accept ``membership``,
    which is not editable — terms pointing at a different person or academy are
    different terms.
    """

    permission_classes = [IsAuthenticated, CanManageOrganizationMemberships]
    http_method_names = ["get", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return OrganizationTeacherConfigurationUpdateSerializer
        return OrganizationTeacherConfigurationSerializer

    @extend_schema(
        responses={
            200: OpenApiResponse(response=OrganizationTeacherConfigurationSerializer),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
            404: OpenApiResponse(
                description="No such teaching terms in this organization."
            ),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=OrganizationTeacherConfigurationUpdateSerializer,
        responses={
            200: OpenApiResponse(response=OrganizationTeacherConfigurationSerializer),
            400: OpenApiResponse(description="Nothing to change, or a value out of range."),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
            404: OpenApiResponse(
                description="No such teaching terms in this organization."
            ),
        },
    )
    def patch(self, request, *args, **kwargs):
        configuration = self.get_object()
        serializer = self.get_serializer(configuration, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        configuration = serializer.save()
        body = OrganizationTeacherConfigurationSerializer(
            configuration, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_200_OK)
