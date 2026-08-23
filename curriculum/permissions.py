"""Role gates for the curriculum API.

Placement review is lead-only for now: sub-teachers are deliberately excluded
(Phase 2 decision — the spec left it open, the product owner chose lead only).
"""

from rest_framework.permissions import BasePermission

from accounts.models import Role


class IsLeadTeacher(BasePermission):
    message = "Only the lead teacher can review placements."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.LEAD)


class IsStudent(BasePermission):
    message = "Only a student account has placements."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.STUDENT)
