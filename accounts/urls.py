"""Account URLs, mounted at /api/accounts/."""

from django.urls import path

from .views import MeView, MyChildrenView, ParentLinkCreateView

app_name = "accounts"

urlpatterns = [
    path("me/", MeView.as_view(), name="me"),
    path("my-children/", MyChildrenView.as_view(), name="my-children"),
    path("parent-links/", ParentLinkCreateView.as_view(), name="parent-link-create"),
]
