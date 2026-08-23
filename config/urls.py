"""Root URL configuration.

Auth lives under /api/auth/ (registration is ours, login/logout is
dj-rest-auth); account resources under /api/accounts/, curriculum and placement
under /api/curriculum/, availability and bookings under /api/scheduling/.
"""

from dj_rest_auth.views import LoginView, LogoutView
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from accounts.views import RegisterView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Auth
    path("api/auth/register/", RegisterView.as_view(), name="rest_register"),
    path("api/auth/login/", LoginView.as_view(), name="rest_login"),
    path("api/auth/logout/", LogoutView.as_view(), name="rest_logout"),
    # Resources
    path("api/accounts/", include("accounts.urls")),
    path("api/curriculum/", include("curriculum.urls")),
    path("api/scheduling/", include("scheduling.urls")),
    # Schema / docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]

# Placement audio is served by Django in development only; a real deployment
# puts MEDIA_ROOT behind the web server or object storage (see tech-debt.md).
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
