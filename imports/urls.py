from django.urls import path
from .views import ImportValidateView, ImportCommitView

app_name = "imports"

urlpatterns = [
    path("organizations/<int:organization_pk>/validate/", ImportValidateView.as_view(), name="validate"),
    path("organizations/<int:organization_pk>/<int:pk>/commit/", ImportCommitView.as_view(), name="commit"),
]
