"""Curriculum URLs, mounted at /api/curriculum/.

Two groups, and the split is the point of SaaS Phase 3.

Everything that reads or writes an academy's data is addressed through that
academy, following the convention SaaS Phase 2 set:

.. code-block:: text

    /api/<domain>/organizations/<organization_id>/<resource>/

The tenant is part of the address, which is what lets one mixin resolve it to the
caller's verified membership and one permission class ask "is this caller inside
*this* academy" (see ``organizations.views.OrganizationScopedMixin``). Each domain
keeps its own routes rather than writing into ``organizations/urls.py``.

The one global route left is the placement-audio download, and it is global on
purpose: the token in its query string is a bearer capability minted only after the
academy-scoped ``audio-url/`` endpoint has authorised the requester, so adding a
tenant to the path would be a check that proves nothing (see the view).

**Retired here.** ``GET /api/curriculum/tracks/`` was public and listed every
track in the database; the placement create, ``mine/``, ``pending/``, ``review/``
and ``audio-url/`` routes were global and let any lead teacher anywhere reach any
placement. They are removed rather than kept alongside the academy-scoped ones —
no client depends on them (the frontend is a later phase), and leaving a second,
unscoped way in is how a tenant boundary quietly stops holding.

Literal segments sit above id routes, the repository's usual ordering.
"""

from django.urls import path

from .views import (
    AcademyChildrenPlacementListView,
    AcademyLevelDetailView,
    AcademyLevelListCreateView,
    AcademyMyPlacementListView,
    AcademyMyTeacherTrackListView,
    AcademyPendingPlacementListView,
    AcademyPlacementAudioURLView,
    AcademyPlacementCreateView,
    AcademyPlacementReviewView,
    AcademyTeacherTrackDetailView,
    AcademyTeacherTrackListCreateView,
    AcademyTrackDetailView,
    AcademyTrackListCreateView,
    PlacementAudioDownloadView,
)

app_name = "curriculum"

ACADEMY = "organizations/<int:organization_pk>/"

urlpatterns = [
    # --- Tracks and levels ---------------------------------------------------
    path(
        f"{ACADEMY}tracks/",
        AcademyTrackListCreateView.as_view(),
        name="academy-track-list",
    ),
    path(
        f"{ACADEMY}tracks/<int:pk>/",
        AcademyTrackDetailView.as_view(),
        name="academy-track-detail",
    ),
    path(
        f"{ACADEMY}levels/",
        AcademyLevelListCreateView.as_view(),
        name="academy-level-list",
    ),
    path(
        f"{ACADEMY}levels/<int:pk>/",
        AcademyLevelDetailView.as_view(),
        name="academy-level-detail",
    ),
    # --- Teacher curriculum eligibility --------------------------------------
    path(
        f"{ACADEMY}teachers/",
        AcademyTeacherTrackListCreateView.as_view(),
        name="academy-teacher-track-list",
    ),
    path(
        f"{ACADEMY}teachers/mine/",
        AcademyMyTeacherTrackListView.as_view(),
        name="academy-teacher-track-mine",
    ),
    path(
        f"{ACADEMY}teachers/<int:pk>/",
        AcademyTeacherTrackDetailView.as_view(),
        name="academy-teacher-track-detail",
    ),
    # --- Placements ----------------------------------------------------------
    path(
        f"{ACADEMY}placements/",
        AcademyPlacementCreateView.as_view(),
        name="academy-placement-create",
    ),
    path(
        f"{ACADEMY}placements/mine/",
        AcademyMyPlacementListView.as_view(),
        name="academy-placement-mine",
    ),
    path(
        f"{ACADEMY}placements/children/",
        AcademyChildrenPlacementListView.as_view(),
        name="academy-placement-children",
    ),
    path(
        f"{ACADEMY}placements/pending/",
        AcademyPendingPlacementListView.as_view(),
        name="academy-placement-pending",
    ),
    path(
        f"{ACADEMY}placements/<int:pk>/review/",
        AcademyPlacementReviewView.as_view(),
        name="academy-placement-review",
    ),
    path(
        f"{ACADEMY}placements/<int:pk>/audio-url/",
        AcademyPlacementAudioURLView.as_view(),
        name="academy-placement-audio-url",
    ),
    # --- Bearer-token audio download (deliberately not tenant-scoped) --------
    path(
        "placements/<int:pk>/audio/",
        PlacementAudioDownloadView.as_view(),
        name="placement-audio-download",
    ),
]
