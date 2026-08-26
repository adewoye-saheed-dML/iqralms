"""API views for the curriculum app — the Phase 2 surface, plus Phase 6's
private access to placement audio.

Tracks and levels are read-only here: they are curriculum data the lead teacher
maintains through the admin, the same call Phase 1 made for teacher profiles.

Phase 6 adds the two views that replace the old public media route:

* ``PlacementAudioURLView`` decides *who* may hear a sample and mints a
  short-lived URL for them. This is where authorisation happens.
* ``PlacementAudioDownloadView`` serves the bytes when the storage backend
  cannot sign its own URLs (development and tests). In production the minted URL
  points straight at the private bucket and this view is never reached.
"""

import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Role

from .audio import (
    AUDIO_TOKEN_PARAM,
    PlacementAudioUnavailable,
    placement_audio_access,
    read_audio_token,
)
from .exceptions import PlacementAlreadyReviewed
from .models import PlacementResult, Status, Track
from .permissions import IsLeadTeacher, IsLeadTeacherOrStudent, IsStudent
from .serializers import (
    PlacementAudioAccessSerializer,
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


class PlacementAudioURLView(generics.GenericAPIView):
    """GET /api/curriculum/placements/{id}/audio-url/ — temporary access.

    Phase 6's replacement for the public media path. Returns a URL that expires;
    minting it is the authorisation step, so everything about *who* may hear a
    recitation sample is decided here.

    The access rule (product owner, 2026-08-26): the lead teacher, who reviews
    the sample, and the student whose voice it is. Sub-teachers and parents get
    403 — deliberately not added, because Phase 6 hardens what exists and must
    not widen who can reach student data.

    Why a student asking for another student's sample gets 404 and not 403: the
    queryset is narrowed by role, so an unauthorised placement is simply not
    there. A 403 would confirm the row exists, which is a small leak the
    endpoint has no reason to hand out.
    """

    serializer_class = PlacementAudioAccessSerializer
    permission_classes = [IsAuthenticated, IsLeadTeacherOrStudent]

    def get_queryset(self):
        placements = PlacementResult.objects.select_related("student", "track")
        if self.request.user.role == Role.STUDENT:
            return placements.filter(student=self.request.user)
        # Lead teacher: any placement, because reviewing is the job.
        return placements

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=PlacementAudioAccessSerializer,
                description=(
                    "A short-lived URL for the recitation sample. It expires; "
                    "ask again rather than storing it."
                ),
            ),
            403: OpenApiResponse(
                description="Not the lead teacher and not a student."
            ),
            404: OpenApiResponse(
                description=(
                    "No such placement, not this student's placement, or a "
                    "beginner skip with no recording."
                )
            ),
        }
    )
    def get(self, request, *args, **kwargs):
        placement = self.get_object()
        try:
            url, expires_at = placement_audio_access(placement, request=request)
        except PlacementAudioUnavailable as exc:
            raise Http404(str(exc)) from exc
        return Response(
            {
                "url": url,
                "expires_at": expires_at,
                "expires_in": settings.PLACEMENT_AUDIO_URL_TTL_SECONDS,
            },
            status=status.HTTP_200_OK,
        )


class PlacementAudioDownloadView(APIView):
    """GET /api/curriculum/placements/{id}/audio/?token=... — the local path.

    Only used when the storage backend cannot sign its own URLs, i.e. local
    private storage in development and tests. Against a private bucket the
    minted URL is presigned and points at the bucket, so nothing reaches here.

    ``AllowAny`` and no session required, and that is not a gap: the token is a
    bearer capability standing in for a presigned URL, and it is minted only
    after ``PlacementAudioURLView`` has authorised the requester. Making this
    view re-authenticate would defeat the purpose — an ``<audio src>`` element
    sends no Authorization header, which is exactly why presigned URLs exist.

    What it does check, on every request:

    * the token's signature and its age (``read_audio_token``);
    * that the token names *this* placement — a token for one sample cannot be
      pointed at another by editing the path;
    * that the placement still holds the object the token was minted for, so a
      re-submitted sample invalidates outstanding tokens immediately rather than
      at expiry.

    Every failure is the same 404. Telling the caller which check failed would
    let someone probing tokens narrow down why.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name=AUDIO_TOKEN_PARAM,
                location=OpenApiParameter.QUERY,
                required=True,
                description="The signed token from the audio-url endpoint.",
            )
        ],
        responses={
            200: OpenApiResponse(description="The audio file itself."),
            404: OpenApiResponse(
                description="Missing, invalid, expired or superseded token."
            ),
        },
    )
    def get(self, request, pk):
        if getattr(default_storage, "provides_signed_urls", False):
            # The active backend signs its own URLs, so this route is not part
            # of the access path at all. Refusing rather than serving keeps
            # exactly one way in per deployment.
            raise Http404("Placement audio is served by the storage backend.")

        payload = read_audio_token(request.query_params.get(AUDIO_TOKEN_PARAM, ""))
        if payload is None or payload.get("placement") != pk:
            raise Http404("Invalid or expired placement-audio token.")

        placement = get_object_or_404(PlacementResult, pk=pk)
        audio = placement.audio_sample
        if not audio or audio.name != payload.get("name"):
            raise Http404("That recitation sample is no longer the current one.")

        # Streamed from private storage, never from a public route.
        return FileResponse(
            audio.storage.open(audio.name, "rb"),
            as_attachment=False,
            filename=os.path.basename(audio.name),
        )
