"""API views for the accounts app — the Phase 1 surface, nothing more."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import Role, User
from .serializers import (
    LinkedStudentSerializer,
    ParentLinkCreateSerializer,
    RegisterSerializer,
    UserSerializer,
)

#: Returned to a minor student who has no parent linked yet.
STATUS_PENDING_PARENT_LINK = "pending_parent_link"
STATUS_ACTIVE = "active"


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register/ — create a user.

    A minor student comes back with status ``pending_parent_link``: the account
    exists and can log in, but is not complete until a parent links to it.
    """

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                description=(
                    "Created. Body carries the user, an account status of "
                    "'active' or 'pending_parent_link', and a human-readable "
                    "detail message."
                )
            )
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        pending = not user.is_fully_active
        if pending:
            detail = (
                "Account created. A parent must link to this student using "
                f"signup code {user.signup_code} before it can be used."
            )
        else:
            detail = "Account created."

        body = {
            "user": UserSerializer(user, context=self.get_serializer_context()).data,
            "status": STATUS_PENDING_PARENT_LINK if pending else STATUS_ACTIVE,
            "detail": detail,
        }
        return Response(body, status=status.HTTP_201_CREATED)


class MeView(generics.RetrieveAPIView):
    """GET /api/accounts/me/ — the logged-in user's own profile."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        # select_related so a teacher's profile is one query, not two.
        return (
            User.objects.select_related("teacher_profile")
            .get(pk=self.request.user.pk)
        )


class MyChildrenView(generics.ListAPIView):
    """GET /api/accounts/my-children/ — students linked to the calling parent."""

    serializer_class = LinkedStudentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role != Role.PARENT:
            raise PermissionDenied("Only a parent account has linked children.")
        return User.objects.filter(parent_links__parent=user).order_by("username")


class ParentLinkCreateView(generics.CreateAPIView):
    """POST /api/accounts/parent-links/ — parent links to a student by code."""

    serializer_class = ParentLinkCreateSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        if self.request.user.role != Role.PARENT:
            raise PermissionDenied("Only a parent account can create a parent link.")

        code = serializer.validated_data["student_code"]
        try:
            student = User.objects.get(signup_code=code, role=Role.STUDENT)
        except User.DoesNotExist:
            # Deliberately the same response whether the code is unknown or
            # belongs to a non-student, so this cannot be used to probe accounts.
            raise NotFound("No student found for that signup code.")

        serializer.save(student=student)
