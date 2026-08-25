"""API views for the pricing app — the Phase 5 surface, nothing more.

Three endpoints, and the interesting thing about them is who is on which side of
the wall: the lead writes rates and reads the whole history; a student reads only
their own live rate, without the note explaining it. Nothing here charges anyone
— a ``PricingAgreement`` records what *should* be charged, and a future payments
phase is what reads it.
"""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

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


class PricingAgreementView(generics.ListCreateAPIView):
    """/api/pricing/agreements/ — the lead records rates, and reads them back.

    Both halves of the spec's surface on one path, because they are one resource:

    * **POST** records a pricing exception. Lead-only, with no sub-teacher path at
      all: this is the one lever that moves the lead's own margin (mvp-spec
      section 3). Creating an agreement for a student and level that already has a
      live one supersedes the old row rather than editing or deleting it, so the
      history survives.
    * **GET ?student_id=** lists one student's history — active *and* superseded,
      newest first, which is the point of keeping the old rows: "what do we charge
      them, and what did we charge them before" is one question.
    """

    permission_classes = [IsAuthenticated, IsLeadTeacher]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return PricingAgreementCreateSerializer
        return PricingAgreementSerializer

    def get_queryset(self):
        student_id = required_int_param(self.request, "student_id")
        # An unknown student is an empty list rather than a 404, matching the
        # availability and open-cohort endpoints: "what have we agreed with this
        # person" and "does this person exist" are separate questions.
        return PricingAgreement.objects.filter(student_id=student_id).select_related(
            *AGREEMENT_RELATED
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="student_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Whose pricing history to list.",
            )
        ],
        responses={
            200: OpenApiResponse(response=PricingAgreementSerializer(many=True)),
            400: OpenApiResponse(description="student_id missing or not an integer."),
            403: OpenApiResponse(description="Not the lead teacher."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
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
            403: OpenApiResponse(description="Not the lead teacher."),
        }
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


class MyPricingAgreementListView(generics.ListAPIView):
    """GET /api/pricing/agreements/mine/ — the student's own live rates.

    One row per level at most, because only active agreements are listed: a family
    reads what they pay now, not the negotiation that got them there. ``notes`` is
    absent from this shape entirely (see serializers.py).
    """

    serializer_class = MyPricingAgreementSerializer
    permission_classes = [IsAuthenticated, IsStudent]

    def get_queryset(self):
        return PricingAgreement.objects.filter(
            student=self.request.user, active=True
        ).select_related(*AGREEMENT_RELATED)
