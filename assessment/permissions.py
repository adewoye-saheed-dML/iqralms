"""Role gates for the assessment API.

Four roles, four different views of the same rows, and CLAUDE.md is explicit that
"permissions must be enforced server-side, not only through serializer field
omission". So these classes say who may call an endpoint at all, the views narrow
every queryset to the caller's own teacher/student/family context, and the
serializers are the third layer rather than the only one.

Each class is local to this app rather than imported from ``curriculum``,
``scheduling`` or ``pricing``, following the precedent those three set: the rule
is ``role == "lead"`` in all four today, but they answer different questions —
"who reviews placements", "who commits a teacher's time", "who sets what a family
pays", "who inspects teaching quality" — and widening one must not silently widen
the others.
"""

from rest_framework.permissions import BasePermission

from accounts.models import Role


class IsLeadTeacher(BasePermission):
    """Rubric configuration, lead review, teacher reports, snapshot generation.

    All four are the lead's alone. A sub-teacher reading academy-wide quality data
    is the specific thing the phase spec rules out (visibility section), and rubric
    configuration decides what every teacher is measured on, so it is not something
    a teacher grants themselves.
    """

    message = "Only the lead teacher can do this."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.LEAD)


class IsTeacher(BasePermission):
    """Submitting an assessment, and reading one's own submissions.

    Role only. *Which* booking a teacher may assess is not a permission question —
    it is ``SessionAssessment.clean()``'s, which requires ``assessed_by ==
    booking.teacher`` — and the view scopes the lookup to the caller's own
    bookings, so another teacher's session is a 404 rather than a 403. A 403 would
    confirm the booking exists.
    """

    message = "Only a teacher account assesses sessions."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_teacher)


class IsStudent(BasePermission):
    """A student reads their own assessments, progress and published snapshots."""

    message = "Only a student account has assessments of its own."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.STUDENT)


class IsParent(BasePermission):
    """A parent reads a linked child's progress — which child is checked per request.

    Wider than the placement-audio gate, which deliberately excludes parents
    (curriculum/permissions.py). Progress is what the phase spec says families
    see, and a minor's parent is the family member who reads it; a recitation
    recording is a different kind of data and that decision stands.
    """

    message = "Only a parent account has linked children."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.PARENT)
