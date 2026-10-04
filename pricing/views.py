"""API views for the pricing app — the Phase 5 surface, nothing more.

Three endpoints, and the interesting thing about them is who is on which side of
the wall: the lead writes rates and reads the whole history; a student reads only
their own live rate, without the note explaining it. Nothing here charges anyone
— a ``PricingAgreement`` records what *should* be charged, and a future payments
phase is what reads it.
"""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Role, User
from organizations.permissions import IsOrganizationMember
from organizations.views import OrganizationScopedMixin

from .models import PricingAgreement
from .permissions import IsLeadTeacher, IsStudent
from .serializers import (
    MyPricingAgreementSerializer,
    PricingAgreementCreateSerializer,
    PricingAgreementSerializer,
)

#: One query for the agreement plus everything the read serializers touch.
AGREEMENT_RELATED = ("student", "level", "level__track", "approved_by")


def required_int_param(request, name):
    """Read a required integer query parameter, or raise a 400 explaining why.

    A local copy of the helper ``scheduling.views`` has. Without the parameter the
    honest behaviour of the listing below would be dumping every family's
    negotiated rate in the academy, which is a different endpoint than the one the
    spec asks for.
    """
    raw = request.query_params.get(name)
    if not raw:
        raise ValidationError({name: ["This query parameter is required."]})
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: ["Must be an integer."]})


class AcademyScopedView(OrganizationScopedMixin):
    """Shared plumbing for the academy-scoped pricing views.

    The organization comes from the URL kwarg ``organization_pk`` and is resolved
    to the caller's membership by the parent mixin; and it is put into the
    serializer context, which is how write serializers narrow their querysets
    to one tenant.
    """

    organization_url_kwarg = "organization_pk"

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.caller_membership:
            context["organization"] = self.organization
        return context


class PricingAgreementView(AcademyScopedView, generics.ListCreateAPIView):
    """/api/pricing/organizations/<organization_pk>/agreements/ — the lead records rates, and reads them back.

    Both halves of the spec's surface on one path, because they are one resource:

    * **POST** records a pricing exception. Lead-only, with no sub-teacher path at
      all: this is the one lever that moves the lead's own margin (mvp-spec
      section 3). Creating an agreement for a student and level that already has a
      live one supersedes the old row rather than editing or deleting it, so the
      history survives.
    * **GET ?student_id=** lists one student's history in this academy — active
      *and* superseded, newest first.
    """

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]


    def get_serializer_class(self):
        if self.request.method == "POST":
            return PricingAgreementCreateSerializer
        return PricingAgreementSerializer

    def get_queryset(self):
        student_id = required_int_param(self.request, "student_id")
        # An unknown student is an empty list rather than a 404, matching the
        # availability and open-cohort endpoints: "what have we agreed with this
        # person" and "does this person exist" are separate questions.
        return (
            PricingAgreement.objects.in_organization(self.organization)
            .filter(student_id=student_id)
            .select_related(*AGREEMENT_RELATED)
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="organization_pk",
                type=int,
                location=OpenApiParameter.PATH,
                required=True,
                description="The academy this agreement belongs to.",
            ),
            OpenApiParameter(
                name="student_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Whose pricing history to list.",
            ),
        ],
        responses={
            200: OpenApiResponse(response=PricingAgreementSerializer(many=True)),
            400: OpenApiResponse(description="student_id missing or not an integer."),
            403: OpenApiResponse(description="Not an active member or not the lead teacher."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="organization_pk",
                type=int,
                location=OpenApiParameter.PATH,
                required=True,
                description="The academy this agreement belongs to.",
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=PricingAgreementSerializer,
                description=(
                    "Created and active. Any previous agreement for this student "
                    "and level is now active=False, still queryable."
                ),
            ),
            400: OpenApiResponse(
                description="Not a student, an unknown level, or a negative rate."
            ),
            403: OpenApiResponse(description="Not an active member or not the lead teacher."),
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agreement = serializer.save()
        body = PricingAgreementSerializer(
            agreement, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_201_CREATED)


class MyPricingAgreementListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/pricing/organizations/<organization_pk>/agreements/mine/ — the student's own live rates.

    One row per level at most, because only active agreements are listed: a family
    reads what they pay now, not the negotiation that got them there. ``notes`` is
    absent from this shape entirely (see serializers.py).
    """

    serializer_class = MyPricingAgreementSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudent]

    def get_queryset(self):
        return (
            PricingAgreement.objects.in_organization(self.organization)
            .filter(student=self.request.user, active=True)
            .select_related(*AGREEMENT_RELATED)
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="organization_pk",
                type=int,
                location=OpenApiParameter.PATH,
                required=True,
                description="The academy to read agreements for.",
            ),
        ],
        responses={
            200: OpenApiResponse(response=MyPricingAgreementSerializer(many=True)),
            403: OpenApiResponse(description="Not an active member or not a student."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


from django.shortcuts import get_object_or_404
from organizations.models import MembershipStatus, Organization, OrganizationRole


@extend_schema(
    summary="Get active pricing agreement for a linked child in an academy (Parent only)",
    parameters=[
        OpenApiParameter(
            name="organization_pk",
            type=int,
            location=OpenApiParameter.PATH,
            required=False,
            description="The academy to read child agreement for.",
        ),
        OpenApiParameter(
            name="student_id",
            type=int,
            location=OpenApiParameter.PATH,
            required=True,
            description="The student ID.",
        ),
        OpenApiParameter(
            name="organization_id",
            type=int,
            location=OpenApiParameter.QUERY,
            required=False,
            description="The academy ID (required if organization_pk is not in path).",
        ),
    ],
    responses={
        200: OpenApiResponse(response=MyPricingAgreementSerializer),
        400: OpenApiResponse(description="organization_id is required."),
        403: OpenApiResponse(description="Only linked parent can view child's active agreement, or student is not in this academy."),
        404: OpenApiResponse(description="No active agreement found for this child in this academy."),
    },
)
class ChildActivePricingAgreementView(generics.GenericAPIView):
    """GET active agreement for a linked child strictly scoped to an academy."""

    permission_classes = [IsAuthenticated]
    serializer_class = MyPricingAgreementSerializer

    def get(self, request, student_id, organization_pk=None, *args, **kwargs):
        user = request.user
        if user.role != Role.PARENT:
            raise PermissionDenied("Only parents can access child pricing agreements.")

        from accounts.models import ParentLink
        is_linked = ParentLink.objects.filter(parent=user, student_id=student_id).exists()
        if not is_linked:
            raise PermissionDenied("You are not linked to this student.")

        org_id = organization_pk or request.query_params.get("organization_id") or request.query_params.get("organization")
        if not org_id:
            raise ValidationError({"organization_id": "Organization ID is required to fetch active child agreement."})

        organization = get_object_or_404(Organization, pk=org_id)
        student = get_object_or_404(User, pk=student_id, role=Role.STUDENT)

        from accounts.tenancy import is_active_student_participant
        if not is_active_student_participant(user=student, organization=organization):
            raise PermissionDenied("Student is not an active participant of this academy.")

        agreement = (
            PricingAgreement.objects.in_organization(organization)
            .filter(
                student_id=student_id,
                active=True,
            )
            .select_related("student", "level", "level__track", "approved_by")
            .first()
        )
        if not agreement:
            return Response(
                {"detail": "No active pricing agreement found for this child in this academy."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = self.get_serializer(agreement)
        return Response(serializer.data, status=status.HTTP_200_OK)

