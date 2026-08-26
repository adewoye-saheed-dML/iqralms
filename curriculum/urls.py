"""Curriculum URLs, mounted at /api/curriculum/.

The two audio routes are Phase 6's replacement for the ``MEDIA_URL`` route that
used to sit in ``config/urls.py``:

* ``audio-url/`` authorises the requester and mints a short-lived URL;
* ``audio/`` serves the bytes for a token, and only when the storage backend
  cannot sign its own URLs (development and tests — in production the minted URL
  points at the private bucket instead).

``audio/`` is registered unconditionally rather than behind a settings check, so
that ``reverse()`` works and the tests are the same shape in every environment.
The view itself refuses when the active backend signs its own URLs.
"""

from django.urls import path

from .views import (
    MyPlacementListView,
    PendingPlacementListView,
    PlacementAudioDownloadView,
    PlacementAudioURLView,
    PlacementCreateView,
    PlacementReviewView,
    TrackListView,
)

app_name = "curriculum"

urlpatterns = [
    path("tracks/", TrackListView.as_view(), name="track-list"),
    path("placements/", PlacementCreateView.as_view(), name="placement-create"),
    path("placements/mine/", MyPlacementListView.as_view(), name="placement-mine"),
    path(
        "placements/pending/",
        PendingPlacementListView.as_view(),
        name="placement-pending",
    ),
    path(
        "placements/<int:pk>/review/",
        PlacementReviewView.as_view(),
        name="placement-review",
    ),
    path(
        "placements/<int:pk>/audio-url/",
        PlacementAudioURLView.as_view(),
        name="placement-audio-url",
    ),
    path(
        "placements/<int:pk>/audio/",
        PlacementAudioDownloadView.as_view(),
        name="placement-audio-download",
    ),
]
