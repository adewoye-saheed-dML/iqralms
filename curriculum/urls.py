"""Curriculum URLs, mounted at /api/curriculum/."""

from django.urls import path

from .views import (
    MyPlacementListView,
    PendingPlacementListView,
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
]
