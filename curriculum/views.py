"""API views for the curriculum app — the Phase 2 surface, nothing more.

Tracks and levels are read-only here: they are curriculum data the lead teacher
maintains through the admin, the same call Phase 1 made for teacher profiles.
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .exceptions import PlacementAlreadyReviewed
from .models import PlacementResult, Status, Track
from .permissions import IsLeadTeacher, IsStudent
from .serializers import (
    PlacementResultSerializer,
    PlacementReviewSerializer,
    PlacementSubmitSerializer,
    TrackSerializer,
)


class Conflict(APIException):
    """409 — the request is valid but the resource is in the wrong state."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This placement has already been reviewed."
    default_code = "already_reviewed"


#: One query for the placement plus everything the read serializer touches.
PLACEMENT_RELATED = ("student", "track", "recommended_level", "reviewed_by")


class TrackListView(generics.ListAPIView):
    """GET /api/curriculum/tracks/ — public list, levels nested in order."""

    serializer_class = TrackSerializer
    permission_classes = [AllowAny]
    # Level.Meta.ordering already sorts by (track, order); prefetch keeps the
    # nested levels to a single extra query rather than one per track.
    queryset = Track.objects.prefetch_related("levels")


class PlacementCreateView(generics.CreateAPIView):
    """POST /api/curriculum/placements/ — student submits audio or a skip.

    Re-submitting for a track the student already has a placement for updates
    that row back to pending; it never creates a second one. A minor with no
    parent link yet may still submit — placement is a pre-enrolment step, and
    ``is_fully_active`` gates booking (Phase 3), not this.
    """

    serializer_class = PlacementSubmitSerializer
    permission_classes = [IsAuthenticated, IsStudent]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=PlacementResultSerializer,
                description=(
                    "Created or updated. A beginner skip comes back already "
                    "'reviewed' with the track's first level and no reviewer."
                ),
            )
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        placement = serializer.save()
        body = PlacementResultSerializer(
            placement, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_201_CREATED)


class MyPlacementListView(generics.ListAPIView):
    """GET /api/curriculum/placements/mine/ — the student's own placements."""

    serializer_class = PlacementResultSerializer
    permission_classes = [IsAuthenticated, IsStudent]

    def get_queryset(self):
        return PlacementResult.objects.filter(
            student=self.request.user
        ).select_related(*PLACEMENT_RELATED)


class PendingPlacementListView(generics.ListAPIView):
    """GET /api/curriculum/placements/pending/ — the lead's review queue."""

    serializer_class = PlacementResultSerializer
    permission_classes = [IsAuthenticated, IsLeadTeacher]

    def get_queryset(self):
        return PlacementResult.objects.filter(status=Status.PENDING).select_related(
            *PLACEMENT_RELATED
        )


class PlacementReviewView(generics.GenericAPIView):
    """POST /api/curriculum/placements/{id}/review/ — lead sets the level.

    Review is a one-way transition: an already-reviewed placement returns 409.
    Correcting a level means the admin, or the student re-submitting.
    """

    serializer_class = PlacementReviewSerializer
    permission_classes = [IsAuthenticated, IsLeadTeacher]
    # Deliberately not filtered to pending, so reviewing a reviewed placement
    # is a 409 rather than a misleading 404.
    queryset = PlacementResult.objects.select_related(*PLACEMENT_RELATED)

    @extend_schema(
        request=PlacementReviewSerializer,
        responses={
            200: OpenApiResponse(response=PlacementResultSerializer),
            409: OpenApiResponse(description="Already reviewed."),
        },
    )
    def post(self, request, *args, **kwargs):
        placement = self.get_object()
        serializer = self.get_serializer(placement, data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            placement = serializer.save()
        except PlacementAlreadyReviewed as exc:
            raise Conflict(str(exc)) from exc
        body = PlacementResultSerializer(
            placement, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_200_OK)
