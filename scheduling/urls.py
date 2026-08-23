"""Scheduling URLs, mounted at /api/scheduling/."""

from django.urls import path

from .views import (
    AvailabilityListView,
    BookingCancelView,
    BookingCreateView,
    MyBookingListView,
    TeachingBookingListView,
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
]
