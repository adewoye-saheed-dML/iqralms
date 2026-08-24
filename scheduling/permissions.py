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


class IsLeadTeacher(BasePermission):
    """Opening a group class is the lead's call, not a sub-teacher's.

    Deliberately a separate class from ``curriculum.permissions.IsLeadTeacher``
    rather than an import: same rule today, but the two gates answer different
    questions ("who reviews placements" vs "who commits a teacher's time to a
    cohort") and should be free to diverge without one silently dragging the
    other along.
    """

    message = "Only the lead teacher can create a cohort."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.LEAD)
