"""Role gates for the curriculum API.

Placement review is lead-only for now: sub-teachers are deliberately excluded
(Phase 2 decision — the spec left it open, the product owner chose lead only).

Phase 6 adds one gate, for the placement-audio access endpoint. Who may hear a
recitation sample was put to the product owner rather than guessed (2026-08-26):
the lead teacher, who reviews it, and the student whose voice it is. Not
sub-teachers, and not a minor's linked parent — that would be a new permission
neither Phase 2 nor this phase's spec asks for, and Phase 6 is a hardening
phase, so it must not widen who can reach student data.
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


class IsLeadTeacherOrStudent(BasePermission):
    """The two roles that may ask for access to a recitation sample.

    Role only. *Which* placements a student may reach is a queryset question,
    not a permission one, and the view narrows it to their own rows — so
    another student's sample is a 404 rather than a 403. That distinction is
    deliberate: a 403 would confirm the placement exists.
    """

    message = "Only the lead teacher or the student themselves can hear a sample."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role in {Role.LEAD, Role.STUDENT}
        )
