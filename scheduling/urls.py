"""Scheduling URLs, mounted at /api/scheduling/."""

from django.urls import path

from .views import (
    AcademyBookingListView,
    AvailabilityListView,
    BookingCancelView,
    BookingCreateView,
    BookingMeetingView,
    CohortCreateView,
    CohortDetailView,
    CohortListCreateView,
    MyBookingListView,
    MyWaitlistListView,
    OpenCohortListView,
    RouteView,
    TeacherWaitlistListView,
    TeachingBookingListView,
    WaitlistPromoteView,
)

app_name = "scheduling"

ACADEMY = "organizations/<int:organization_pk>/"

urlpatterns = [
    path(
        f"{ACADEMY}availability/",
        AvailabilityListView.as_view(),
        name="availability-list",
    ),
    path(
        f"{ACADEMY}bookings/",
        BookingCreateView.as_view(),
        name="booking-create",
    ),
    path(
        f"{ACADEMY}bookings/mine/",
        MyBookingListView.as_view(),
        name="booking-mine",
    ),
    path(
        f"{ACADEMY}bookings/teaching/",
        TeachingBookingListView.as_view(),
        name="booking-teaching",
    ),
    path(
        f"{ACADEMY}bookings/academy/",
        AcademyBookingListView.as_view(),
        name="booking-academy",
    ),
    path(
        f"{ACADEMY}bookings/<int:pk>/cancel/",
        BookingCancelView.as_view(),
        name="booking-cancel",
    ),
    path(
        f"{ACADEMY}bookings/<int:pk>/meeting/",
        BookingMeetingView.as_view(),
        name="booking-meeting",
    ),
    # Phase 4 — the system picks the teacher, and group classes exist.
    path(
        f"{ACADEMY}route/",
        RouteView.as_view(),
        name="route",
    ),
    path(
        f"{ACADEMY}cohorts/",
        CohortListCreateView.as_view(),
        name="cohort-create",
    ),
    path(
        f"{ACADEMY}cohorts/<int:pk>/",
        CohortDetailView.as_view(),
        name="cohort-detail",
    ),
    path(
        f"{ACADEMY}cohorts/open/",
        OpenCohortListView.as_view(),
        name="cohort-open",
    ),
    # Phase 5 — a named teacher who is full becomes a tracked promise.
    path(
        f"{ACADEMY}waitlist/mine/",
        MyWaitlistListView.as_view(),
        name="waitlist-mine",
    ),
    path(
        f"{ACADEMY}waitlist/for-teacher/",
        TeacherWaitlistListView.as_view(),
        name="waitlist-for-teacher",
    ),
    path(
        f"{ACADEMY}waitlist/<int:pk>/promote/",
        WaitlistPromoteView.as_view(),
        name="waitlist-promote",
    ),
]
