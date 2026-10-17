"""URL routing for Platform Creator / Super Admin Command Center."""

from django.urls import path
from .platform_admin_views import (
    PlatformAdminOverviewView,
    PlatformAdminSubscribersView,
    PlatformAdminSubscriberManageView,
    PlatformAdminActivitiesView,
    PlatformAdminOnboardingVideosView,
    PlatformAdminSendOnboardingEmailView,
    PlatformAdminDispatchesView,
    PlatformAdminAnnouncementsView,
)

urlpatterns = [
    path("overview/", PlatformAdminOverviewView.as_view(), name="platform_admin_overview"),
    path("subscribers/", PlatformAdminSubscribersView.as_view(), name="platform_admin_subscribers"),
    path("subscribers/<int:pk>/manage/", PlatformAdminSubscriberManageView.as_view(), name="platform_admin_subscriber_manage"),
    path("activities/", PlatformAdminActivitiesView.as_view(), name="platform_admin_activities"),
    path("onboarding-videos/", PlatformAdminOnboardingVideosView.as_view(), name="platform_admin_onboarding_videos"),
    path("send-onboarding-email/", PlatformAdminSendOnboardingEmailView.as_view(), name="platform_admin_send_onboarding_email"),
    path("onboarding-dispatches/", PlatformAdminDispatchesView.as_view(), name="platform_admin_onboarding_dispatches"),
    path("announcements/", PlatformAdminAnnouncementsView.as_view(), name="platform_admin_announcements"),
]
