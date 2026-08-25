"""Scheduling URLs, mounted at /api/scheduling/."""

from django.urls import path

from .views import (
    AvailabilityListView,
    BookingCancelView,
    BookingCreateView,
    CohortCreateView,
    MyBookingListView,
    MyWaitlistListView,
    OpenCohortListView,
    RouteView,
    TeacherWaitlistListView,
    TeachingBookingListView,
    WaitlistPromoteView,
)

app_name = "scheduling"

urlpatterns = [
    path("availability/", AvailabilityListView.as_view(), name="availability-list"),
    path("bookings/", BookingCreateView.as_view(), name="booking-create"),
    path("bookings/mine/", MyBookingListView.as_view(), name="booking-mine"),
    path(
        "bookings/teaching/",
        TeachingBookingListView.as_view(),
        name="booking-teaching",
    ),
    path(
        "bookings/<int:pk>/cancel/",
        BookingCancelView.as_view(),
        name="booking-cancel",
    ),
    # Phase 4 — the system picks the teacher, and group classes exist.
    path("route/", RouteView.as_view(), name="route"),
    path("cohorts/", CohortCreateView.as_view(), name="cohort-create"),
    path("cohorts/open/", OpenCohortListView.as_view(), name="cohort-open"),
    # Phase 5 — a named teacher who is full becomes a tracked promise.
    path("waitlist/mine/", MyWaitlistListView.as_view(), name="waitlist-mine"),
    path(
        "waitlist/for-teacher/",
        TeacherWaitlistListView.as_view(),
        name="waitlist-for-teacher",
    ),
    path(
        "waitlist/<int:pk>/promote/",
        WaitlistPromoteView.as_view(),
        name="waitlist-promote",
    ),
]
