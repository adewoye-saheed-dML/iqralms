"""Root URL configuration.

Auth lives under /api/auth/ (registration is ours, login/logout is
dj-rest-auth); account resources under /api/accounts/, curriculum and placement
under /api/curriculum/, availability, bookings and the preferred-teacher
waitlist under /api/scheduling/, negotiated rates under /api/pricing/, session
assessment, teacher-quality reporting and family progress under
/api/assessment/, teacher payout records and statements under /api/payouts/, and
the multi-tenant organization foundation — academies and their memberships —
under /api/organizations/.

There is deliberately no media route here any more. Phases 2-5 mounted
``static(settings.MEDIA_URL, ...)`` under DEBUG, which made every placement
recording readable by anyone who knew its path. Phase 6 moved uploads to private
storage; the only way to reach a recitation sample is a short-lived signed URL
from /api/curriculum/placements/{id}/audio-url/. Do not add the route back — the
storage backend's ``url()`` raises specifically so that trying to fails loudly.
"""

from dj_rest_auth.views import (
    LoginView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetView,
)
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from accounts.views import RegisterView
from payments.views import UnifiedPaystackWebhookView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Auth
    path("api/auth/register/", RegisterView.as_view(), name="rest_register"),
    path("api/auth/login/", LoginView.as_view(), name="rest_login"),
    path("api/auth/logout/", LogoutView.as_view(), name="rest_logout"),
    path(
        "api/auth/password/reset/",
        PasswordResetView.as_view(),
        name="rest_password_reset",
    ),
    path(
        "api/auth/password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="rest_password_reset_confirm",
    ),
    path(
        "api/auth/password/reset/confirm/<str:uidb64>/<str:token>/",
        PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    # Resources
    path("api/accounts/", include("accounts.urls")),
    path("api/curriculum/", include("curriculum.urls")),
    path("api/scheduling/", include("scheduling.urls")),
    path("api/pricing/", include("pricing.urls")),
    path("api/assessment/", include("assessment.urls")),
    path("api/payouts/", include("payouts.urls")),
    path("api/organizations/", include("organizations.urls")),
    path("api/organizations/", include("audit_logs.urls")),
    path("api/notifications/", include("notifications.urls")),
    path("api/imports/", include("imports.urls")),
    path("api/billing/", include("billing.urls")),
    path("api/payments/", include("payments.urls")),
    path("api/platform-admin/", include("billing.platform_admin_urls")),
    # Unified Paystack Webhook (Single canonical endpoint for all Paystack events)
    path(
        "api/webhooks/paystack/",
        UnifiedPaystackWebhookView.as_view(),
        name="paystack_unified_webhook",
    ),
    # Alias: Paystack dashboard may be configured to send to this URL
    path(
        "api/payments/webhook/",
        UnifiedPaystackWebhookView.as_view(),
        name="paystack_webhook_alias",
    ),
    # Schema / docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
