"""API views for the curriculum app — one academy at a time.

Every endpoint here except the audio download is addressed through the academy
that owns the data:

.. code-block:: text

    /api/curriculum/organizations/{organization_id}/tracks/
    /api/curriculum/organizations/{organization_id}/tracks/{id}/
    /api/curriculum/organizations/{organization_id}/levels/
    /api/curriculum/organizations/{organization_id}/levels/{id}/
    /api/curriculum/organizations/{organization_id}/teachers/
    /api/curriculum/organizations/{organization_id}/teachers/mine/
    /api/curriculum/organizations/{organization_id}/teachers/{id}/
    /api/curriculum/organizations/{organization_id}/placements/
    /api/curriculum/organizations/{organization_id}/placements/mine/
    /api/curriculum/organizations/{organization_id}/placements/children/
    /api/curriculum/organizations/{organization_id}/placements/pending/
    /api/curriculum/organizations/{organization_id}/placements/{id}/review/
    /api/curriculum/organizations/{organization_id}/placements/{id}/audio-url/

    /api/curriculum/placements/{id}/audio/?token=...     (bearer token, no tenant)

**The URL's organization is input, not authorization.** Each view resolves it to
the caller's own active membership through
``organizations.views.OrganizationScopedMixin`` — which calls
``organizations.active_membership()`` and nothing else — and then scopes its
queryset to that same academy. Both layers are load-bearing, and the second is the
one that decides the status code: a track, level, placement or teacher assignment
belonging to another tenant is **404**, because it is absent from the queryset. A
403 would confirm the row exists somewhere, which is itself a cross-tenant leak.

**What SaaS Phase 3 removed.** The Phase 2 surface was global —
``GET /api/curriculum/tracks/`` was public and returned ``Track.objects.all()``,
and the placement queue handed every pending placement to any lead teacher
anywhere. Both were correct for a single-academy product and are a tenant breach in
a multi-tenant one, so they are gone rather than kept beside the new routes: two
ways in means one of them is eventually forgotten. Only the token-authenticated
audio download stays global, and that is deliberate — see
``PlacementAudioDownloadView``.

Phase 6's private-audio behaviour is otherwise untouched. Minting a URL is still
the authorisation step, the roles that may hear a sample are still the lead teacher
and the student themselves, and this phase adds the academy check in front rather
than widening anything.
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
from accounts.tenancy import children_in_organization
from organizations.permissions import IsOrganizationMember
from organizations.views import OrganizationScopedMixin

from .audio import (
    AUDIO_TOKEN_PARAM,
    PlacementAudioUnavailable,
    placement_audio_access,
    read_audio_token,
)
from .exceptions import PlacementAlreadyReviewed
from .models import Level, PlacementResult, Status, TeacherTrack, Track
from .permissions import (
    CanManageAcademyCurriculum,
    IsAcademyCurriculumManager,
    IsLeadTeacher,
    IsLeadTeacherOrStudent,
    IsParent,
    IsStudent,
    IsTeacher,
)
from .serializers import (
    LevelCreateSerializer,
    LevelSerializer,
    LevelUpdateSerializer,
    PlacementAudioAccessSerializer,
    PlacementResultSerializer,
    PlacementReviewSerializer,
    PlacementSubmitSerializer,
    TeacherTrackCreateSerializer,
    TeacherTrackSerializer,
    TeacherTrackUpdateSerializer,
    TrackSerializer,
    TrackWriteSerializer,
)


class Conflict(APIException):
    """409 — the request is valid but the resource is in the wrong state."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This placement has already been reviewed."
    default_code = "already_reviewed"


#: One query for the placement plus everything the read serializer touches.
PLACEMENT_RELATED = ("student", "track", "recommended_level", "reviewed_by")

#: Reused by every 403 in this module's schema.
NOT_A_MEMBER = OpenApiResponse(
    description="Not an active member of this organization."
)
FOREIGN_TENANT = OpenApiResponse(
    description=(
        "No such object in this organization. A valid id belonging to another "
        "academy answers the same way — the response never confirms that a row "
        "exists elsewhere."
    )
)


class AcademyScopedView(OrganizationScopedMixin):
    """Shared plumbing for the academy-scoped curriculum views.

    Two lines of it, and both matter. The organization comes from the URL kwarg
    ``organization_pk`` and is resolved to the caller's membership by the parent
    mixin; and it is put into the serializer context, which is how the write
    serializers narrow their ``track`` and ``recommended_level`` querysets to one
    tenant. A serializer that had to read the organization from the payload
    instead would be trusting the client with the tenant boundary.
    """

    organization_url_kwarg = "organization_pk"

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["organization"] = self.organization
        return context


# --- Tracks ------------------------------------------------------------------


class AcademyTrackListCreateView(AcademyScopedView, generics.ListCreateAPIView):
    """/api/curriculum/organizations/{id}/tracks/ — the academy's own subjects.

    GET is open to every active member: a student needs the track list to submit a
    placement and a teacher needs it to know what the academy teaches. POST is the
    owner's or an administrator's, because adding a subject is an act of running
    the business (the phase spec leaves teacher authoring to policy; see
    ``permissions.CURRICULUM_MANAGER_ROLES``).

    The created track belongs to the academy in the *route*, whatever the body
    says — there is no ``organization`` field on the write shape at all.
    """

    permission_classes = [
        IsAuthenticated,
        IsOrganizationMember,
        CanManageAcademyCurriculum,
    ]

    def get_queryset(self):
        # Level.Meta.ordering already sorts by (track, order); prefetch keeps the
        # nested levels to a single extra query rather than one per track.
        return Track.objects.filter(
            organization_id=self.organization_id
        ).prefetch_related("levels")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return TrackWriteSerializer
        return TrackSerializer

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=TrackSerializer(many=True),
                description=(
                    "This academy's tracks, each with its levels in order. Never "
                    "another academy's."
                ),
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: NOT_A_MEMBER,
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=TrackWriteSerializer,
        responses={
            201: OpenApiResponse(response=TrackSerializer),
            400: OpenApiResponse(
                description="A slug this academy already uses, or a missing field."
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member, or not this academy's owner or "
                    "administrator."
                )
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        track = serializer.save()
        body = TrackSerializer(track, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_201_CREATED)


class AcademyTrackDetailView(AcademyScopedView, generics.RetrieveUpdateAPIView):
    """GET/PATCH one of this academy's tracks.

    PATCH renames it or changes its slug. It cannot move the track to another
    academy: ``organization`` is not on the write shape, and ``Track.clean()``
    refuses the change even from the admin or a direct ORM write, because moving a
    track carries its levels, placements and teacher assignments into an academy
    that created none of them.

    PUT is not offered — a whole-object replace would have to accept
    ``organization``.
    """

    permission_classes = [
        IsAuthenticated,
        IsOrganizationMember,
        CanManageAcademyCurriculum,
    ]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        return Track.objects.filter(
            organization_id=self.organization_id
        ).prefetch_related("levels")

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return TrackWriteSerializer
        return TrackSerializer

    @extend_schema(
        responses={
            200: OpenApiResponse(response=TrackSerializer),
            401: OpenApiResponse(description="Not authenticated."),
            403: NOT_A_MEMBER,
            404: FOREIGN_TENANT,
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=TrackWriteSerializer,
        responses={
            200: OpenApiResponse(response=TrackSerializer),
            400: OpenApiResponse(description="Nothing to change, or a taken slug."),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member, or not this academy's owner or "
                    "administrator."
                )
            ),
            404: FOREIGN_TENANT,
        },
    )
    def patch(self, request, *args, **kwargs):
        track = self.get_object()
        serializer = self.get_serializer(track, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        track = serializer.save()
        body = TrackSerializer(track, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_200_OK)


# --- Levels ------------------------------------------------------------------


class AcademyLevelScopedView(AcademyScopedView):
    """Levels reached through their track, which is the only thing that owns them.

    ``track__organization`` is the level's academy — there is no
    ``Level.organization`` column to filter on, and deliberately so (see
    ``models.Level.organization``). A level belonging to another tenant is
    therefore absent from the queryset rather than forbidden by it.
    """

    permission_classes = [
        IsAuthenticated,
        IsOrganizationMember,
        CanManageAcademyCurriculum,
    ]

    def get_queryset(self):
        return Level.objects.filter(track__organization_id=self.organization_id)


class AcademyLevelListCreateView(AcademyLevelScopedView, generics.ListCreateAPIView):
    """/api/curriculum/organizations/{id}/levels/ — the academy's ladders.

    Flat rather than nested under a track, with ``?track=<id>`` to narrow it: a
    client rendering an academy's whole curriculum wants one request, and the
    ownership check is no less clear for it because the track is validated against
    the route's academy either way.

    POST appends. The next ``order`` is computed server-side, so "insert at
    position 2" is not a request this API can express — which is how the Phase 2
    append-only rule survives the arrival of a write endpoint.
    """

    def get_queryset(self):
        levels = super().get_queryset()
        track = self.request.query_params.get("track")
        if track:
            # Already scoped to this academy, so a foreign track id narrows the
            # list to nothing rather than widening it.
            levels = levels.filter(track_id=track)
        return levels

    def get_serializer_class(self):
        if self.request.method == "POST":
            return LevelCreateSerializer
        return LevelSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="track",
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Only levels of this track. A track id from another academy "
                    "returns an empty list."
                ),
            )
        ],
        responses={
            200: OpenApiResponse(response=LevelSerializer(many=True)),
            401: OpenApiResponse(description="Not authenticated."),
            403: NOT_A_MEMBER,
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=LevelCreateSerializer,
        responses={
            201: OpenApiResponse(
                response=LevelSerializer,
                description=(
                    "Appended at the track's next order. The first level of a "
                    "track is order 1."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "No such track in this academy — including a valid track id "
                    "owned by a different one."
                )
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member, or not this academy's owner or "
                    "administrator."
                )
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        level = serializer.save()
        body = LevelSerializer(level, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_201_CREATED)


class AcademyLevelDetailView(AcademyLevelScopedView, generics.RetrieveUpdateAPIView):
    """GET/PATCH one level. Its name and its description, never its position."""

    http_method_names = ["get", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return LevelUpdateSerializer
        return LevelSerializer

    @extend_schema(
        responses={
            200: OpenApiResponse(response=LevelSerializer),
            401: OpenApiResponse(description="Not authenticated."),
            403: NOT_A_MEMBER,
            404: FOREIGN_TENANT,
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=LevelUpdateSerializer,
        responses={
            200: OpenApiResponse(response=LevelSerializer),
            400: OpenApiResponse(
                description="Nothing to change; order and track are not editable."
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member, or not this academy's owner or "
                    "administrator."
                )
            ),
            404: FOREIGN_TENANT,
        },
    )
    def patch(self, request, *args, **kwargs):
        level = self.get_object()
        serializer = self.get_serializer(level, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        level = serializer.save()
        body = LevelSerializer(level, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_200_OK)


# --- Teacher curriculum eligibility -------------------------------------------


class AcademyTeacherTrackScopedView(AcademyScopedView):
    """One academy's teacher-track assignments, checked from both ends.

    ``TeacherTrack.objects.in_organization()`` filters on the membership's
    organization *and* the track's, which ``clean()`` guarantees are the same. The
    redundancy is the point: if some future write path broke the invariant, the
    queryset would return nothing rather than start serving a cross-tenant row.
    """

    def get_queryset(self):
        return TeacherTrack.objects.in_organization(
            self.organization_id
        ).select_related("membership__user", "membership__organization", "track")


class AcademyTeacherTrackListCreateView(
    AcademyTeacherTrackScopedView, generics.ListCreateAPIView
):
    """/api/curriculum/organizations/{id}/teachers/ — who may teach what here.

    Owner and admin only, for reading as well as writing — the one curriculum
    endpoint that is not readable by every member. It follows ``accounts``'
    teacher-configurations endpoint rather than the track and level endpoints
    above, because the list names people and what the academy has entrusted them
    with, which is closer to the membership directory than to a syllabus. The
    teacher themselves reads it from ``teachers/mine/``, so nothing here is hidden
    from the person it is about.

    The membership is resolved from a ``user`` id *inside this academy*, so an
    administrator of one academy cannot touch the same teacher's eligibility in
    another — the phase spec's cross-academy teacher requirement, enforced by the
    lookup rather than by a check that could be forgotten.
    """

    permission_classes = [
        IsAuthenticated,
        IsOrganizationMember,
        IsAcademyCurriculumManager,
    ]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return TeacherTrackCreateSerializer
        return TeacherTrackSerializer

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=TeacherTrackSerializer(many=True),
                description="This academy's teacher-track assignments.",
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member, or not this academy's owner or "
                    "administrator."
                )
            ),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=TeacherTrackCreateSerializer,
        responses={
            201: OpenApiResponse(response=TeacherTrackSerializer),
            400: OpenApiResponse(
                description=(
                    "Not a member of this academy, an account that cannot teach, "
                    "a track owned elsewhere, or an assignment that already "
                    "exists."
                )
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member, or not this academy's owner or "
                    "administrator."
                )
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment = serializer.save()
        body = TeacherTrackSerializer(
            assignment, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_201_CREATED)


class AcademyTeacherTrackDetailView(
    AcademyTeacherTrackScopedView, generics.RetrieveUpdateAPIView
):
    """GET/PATCH one assignment — withdraw or restore eligibility.

    An assignment id from another academy is a 404 from the scoped queryset, which
    is what stops an administrator here from deactivating a teacher's eligibility
    there. PUT is not offered: ``membership`` and ``track`` are what the row *is*.
    """

    permission_classes = [
        IsAuthenticated,
        IsOrganizationMember,
        IsAcademyCurriculumManager,
    ]
    http_method_names = ["get", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return TeacherTrackUpdateSerializer
        return TeacherTrackSerializer

    @extend_schema(
        responses={
            200: OpenApiResponse(response=TeacherTrackSerializer),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member, or not this academy's owner or "
                    "administrator."
                )
            ),
            404: FOREIGN_TENANT,
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=TeacherTrackUpdateSerializer,
        responses={
            200: OpenApiResponse(response=TeacherTrackSerializer),
            400: OpenApiResponse(description="Send 'active'."),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member, or not this academy's owner or "
                    "administrator."
                )
            ),
            404: FOREIGN_TENANT,
        },
    )
    def patch(self, request, *args, **kwargs):
        assignment = self.get_object()
        serializer = self.get_serializer(assignment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        assignment = serializer.save()
        body = TeacherTrackSerializer(
            assignment, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_200_OK)


class AcademyMyTeacherTrackListView(
    AcademyTeacherTrackScopedView, generics.ListAPIView
):
    """GET .../teachers/mine/ — what the calling teacher may teach *here*.

    The endpoint that makes the phase visible to a teacher who works for two
    academies. The same account calling it through Academy A's route and Academy
    B's route gets two different lists from the same global identity, because the
    queryset is keyed on the membership the URL resolved to — not on the user.
    """

    serializer_class = TeacherTrackSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsTeacher]

    def get_queryset(self):
        return super().get_queryset().filter(membership=self.caller_membership)

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=TeacherTrackSerializer(many=True),
                description=(
                    "The caller's own track assignments in this academy, and no "
                    "other academy's."
                ),
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description="Not an active member, or not a teaching account."
            ),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# --- Placements ---------------------------------------------------------------


class AcademyPlacementScopedView(AcademyScopedView):
    """Placements of this academy, for students this academy has admitted.

    ``PlacementResult.objects.in_organization()`` is the one definition of that,
    and every placement view below starts from it. It requires both halves — the
    track is owned here *and* the student is an active member here — so a student
    the academy has suspended drops out of its queues without anything else having
    to remember to check.
    """

    def get_queryset(self):
        return PlacementResult.objects.in_organization(
            self.organization_id
        ).select_related(*PLACEMENT_RELATED)


class AcademyPlacementCreateView(AcademyPlacementScopedView, generics.CreateAPIView):
    """POST .../placements/ — a student of this academy submits audio or a skip.

    Two gates, both required. ``IsStudent`` is the account role, unchanged from
    Phase 2; ``IsOrganizationMember`` is the tenant, and it is what stops a student
    enrolled at Academy A from submitting into Academy B by knowing its id. The
    track then resolves inside this academy only, and ``PlacementResult.clean()``
    re-checks the membership at the model layer, so the admin and a direct ORM
    write are held to the same rule.

    Re-submitting for a track the student already has a placement for updates that
    row back to pending; it never creates a second one. A minor with no parent link
    yet may still submit — placement is a pre-enrolment step, and
    ``is_fully_active`` gates booking, not this.
    """

    serializer_class = PlacementSubmitSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudent]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=PlacementResultSerializer,
                description=(
                    "Created or updated. A beginner skip comes back already "
                    "'reviewed' with the track's first level and no reviewer."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "No such track in this academy, both audio and skip, neither "
                    "of them, or a rejected upload."
                )
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description="Not an active member, or not a student account."
            ),
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


class AcademyMyPlacementListView(AcademyPlacementScopedView, generics.ListAPIView):
    """GET .../placements/mine/ — the caller's own placements *in this academy*.

    A student studying at two academies calls this twice, once per route, and gets
    two disjoint lists. Their global set of placements is not something any single
    academy is shown.
    """

    serializer_class = PlacementResultSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudent]

    def get_queryset(self):
        return super().get_queryset().filter(student=self.request.user)

    @extend_schema(
        responses={
            200: OpenApiResponse(response=PlacementResultSerializer(many=True)),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description="Not an active member, or not a student account."
            ),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AcademyChildrenPlacementListView(
    AcademyPlacementScopedView, generics.ListAPIView
):
    """GET .../placements/children/ — a parent's linked children, in this academy.

    Three conditions, and the phase spec is explicit that all three must hold: the
    parent is active here, the child is active here, and a ``ParentLink`` exists.
    ``accounts.tenancy.children_in_organization()`` is where that rule lives and
    this view calls it rather than re-deriving it — a second copy is the one that
    would eventually disagree, and the disagreement would be a parent reading
    another academy's student.

    A ``ParentLink`` is a global family fact, not a key: the same parent sees
    different children through Academy A's route and Academy B's.

    Read-only, and no audio. Phase 6 decided that a recitation sample is for the
    lead teacher and the student themselves, and SaaS Phase 3 is forbidden from
    widening that — so a parent can see *that* their child submitted a sample and
    where they were placed, and cannot hear it.
    """

    serializer_class = PlacementResultSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsParent]

    def get_queryset(self):
        children = children_in_organization(
            parent=self.request.user, organization=self.organization
        )
        return super().get_queryset().filter(student__in=children)

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=PlacementResultSerializer(many=True),
                description=(
                    "Placements of the caller's linked children who are active "
                    "members of this academy. Empty when none are."
                ),
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description="Not an active member, or not a parent account."
            ),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AcademyPendingPlacementListView(
    AcademyPlacementScopedView, generics.ListAPIView
):
    """GET .../placements/pending/ — this academy's review queue.

    The endpoint Phase 2 got most wrong for a multi-tenant platform: it handed
    every pending placement on the platform to any lead teacher who asked. Now the
    queue is the intersection of "a lead teacher" and "a member of this academy",
    and it contains only placements whose track this academy owns and whose student
    it has admitted.
    """

    serializer_class = PlacementResultSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    def get_queryset(self):
        return super().get_queryset().filter(status=Status.PENDING)

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=PlacementResultSerializer(many=True),
                description="Pending placements in this academy only.",
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description="Not an active member, or not the lead teacher."
            ),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AcademyPlacementReviewView(AcademyPlacementScopedView, generics.GenericAPIView):
    """POST .../placements/{id}/review/ — this academy's lead sets the level.

    ``Lead A + Placement A`` is allowed and ``Lead A + Placement B`` is a 404, from
    the scoped queryset rather than from a permission class — the placement is not
    in this academy's queryset, so the response cannot confirm it exists. The
    model refuses the same thing again in ``clean()``: a reviewer must be an active
    member of the academy that owns the track.

    Review is still a one-way transition: an already-reviewed placement returns
    409. Correcting a level means the admin, or the student re-submitting.
    """

    serializer_class = PlacementReviewSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    # Deliberately not filtered to pending, so reviewing a reviewed placement is a
    # 409 rather than a misleading 404.

    @extend_schema(
        request=PlacementReviewSerializer,
        responses={
            200: OpenApiResponse(response=PlacementResultSerializer),
            400: OpenApiResponse(
                description=(
                    "No such level in this academy, or a level from a different "
                    "track than the placement's."
                )
            ),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description="Not an active member, or not the lead teacher."
            ),
            404: FOREIGN_TENANT,
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
        from notifications.services import notify_placement_reviewed
        notify_placement_reviewed(placement)
        body = PlacementResultSerializer(
            placement, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_200_OK)


class AcademyPlacementAudioURLView(
    AcademyPlacementScopedView, generics.GenericAPIView
):
    """GET .../placements/{id}/audio-url/ — temporary access to a sample.

    Phase 6's authorisation step, with the academy check in front of it. Minting
    the URL *is* the authorisation, so the whole question of who may hear a
    recitation sample is decided here, and SaaS Phase 3 answers it with a
    conjunction:

    .. code-block:: text

        authenticated
            + active member of this academy
            + placement belongs to this academy
            + Phase 6's rule: the lead teacher, or the student themselves

    Nothing was widened. Sub-teachers and parents are still refused — a minor's
    voice recording is the last thing a tenancy phase should open up — and the
    roles are exactly Phase 6's.

    Everything unauthorised is a 404 rather than a 403, because the queryset is
    what narrows it: another academy's placement, another student's placement and a
    beginner skip with no recording all answer the same way, and none of them
    confirms that a row exists.
    """

    serializer_class = PlacementAudioAccessSerializer
    permission_classes = [
        IsAuthenticated,
        IsOrganizationMember,
        IsLeadTeacherOrStudent,
    ]

    def get_queryset(self):
        placements = super().get_queryset()
        if self.request.user.role == Role.STUDENT:
            return placements.filter(student=self.request.user)
        # Lead teacher of this academy: any of this academy's placements, because
        # reviewing them is the job.
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
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(
                description=(
                    "Not an active member of this academy, or neither the lead "
                    "teacher nor a student."
                )
            ),
            404: OpenApiResponse(
                description=(
                    "No such placement in this academy, not this student's "
                    "placement, or a beginner skip with no recording."
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

    **Deliberately not organization-scoped, and that is not a hole.** The token is
    a bearer capability standing in for a presigned URL, minted only after
    ``AcademyPlacementAudioURLView`` has checked the caller's membership, the
    placement's academy and the Phase 6 role rule. Requiring a tenant in the path
    here would add a check that proves nothing — the holder of the token is
    authorised by having been given it — while an ``<audio src>`` element cannot
    send an Authorization header, which is exactly why presigned URLs exist.

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
