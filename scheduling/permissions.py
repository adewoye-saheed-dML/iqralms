"""Role gates for the scheduling API.

Booking is the first thing a *parent* acts through rather than only reads, so
the "who may book" gate is wider than Phase 2's student-only placement gate: a
parent books on behalf of a linked child. Which child is still checked per
object, in the serializer — this only says the caller is the right kind of user.
"""

from rest_framework.permissions import BasePermission

from accounts.models import Role


class IsStudent(BasePermission):
    message = "Only a student account has bookings of its own."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.STUDENT)


class IsTeacher(BasePermission):
    message = "Only a teacher account teaches sessions."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_teacher)


class IsStudentOrParent(BasePermission):
    message = "Only a student or a parent of a student can book a session."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role in {Role.STUDENT, Role.PARENT}
        )
