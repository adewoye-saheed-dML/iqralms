"""Role and authority gates for the curriculum API.

Two vocabularies meet in this module, and keeping them apart is the whole of SaaS
Phase 3's security model:

.. code-block:: text

    accounts.Role              lead / sub / student / parent
                               what kind of account this is, globally

    OrganizationRole           owner / admin / staff / teacher
                               what authority this person has inside one academy

Neither answers a tenant question on its own. ``User.role == lead`` means "a lead
teacher somewhere", which in a multi-tenant platform authorizes nothing; an
``admin`` membership means authority *here* and says nothing about whether the
person is a teaching account. So every privileged curriculum endpoint is gated on
a **pair**: the organization membership resolved from the URL (via
``organizations.views.OrganizationScopedMixin``, which reads
``organizations.active_membership()`` and nothing else), plus whichever account
role the operation needs.

The two classes carried over from Phase 2 and Phase 6 — ``IsLeadTeacher``,
``IsStudent``, ``IsLeadTeacherOrStudent`` — are unchanged and still role-only.
That is not a gap: they are never used alone any more. Each sits beside
``IsOrganizationMember`` in the view's ``permission_classes``, so "a lead teacher"
and "a member of this academy" are both required, and the queryset is scoped to
the academy on top of that. Phase 2's Phase-1-era habit of treating
``Role.LEAD`` as academy authority is what tech-debt.md records across five
apps; this app is the first to stop doing it.

A foreign tenant's object is a 404 from a scoped queryset, never a 403 from here.
A 403 would confirm that the row exists.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.models import Role
from organizations.models import OrganizationRole

#: Organization roles that may author an academy's curriculum. The phase spec
#: marks teacher authoring "policy-dependent"; the product owner chose owner and
#: admin (2026-09-05), the same narrower default Phase 1 took for the membership
#: directory — a rule that can be widened later without a migration or a security
#: review, which the reverse is not.
CURRICULUM_MANAGER_ROLES = frozenset({OrganizationRole.OWNER, OrganizationRole.ADMIN})


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


class IsParent(BasePermission):
    message = "Only a parent account has linked children."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.PARENT)


class IsTeacher(BasePermission):
    """A teaching account — ``lead`` or ``sub``, the pair that may hold tracks.

    Reads ``User.is_teacher`` rather than comparing roles, so this and
    ``TeacherTrack.clean()`` cannot drift apart about who can teach.
    """

    message = "Only a teaching account has track assignments."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_teacher)


class IsLeadTeacherOrStudent(BasePermission):
    """The two roles that may ask for access to a recitation sample.

    Role only. *Which* placements a caller may reach is a queryset question, not a
    permission one — the view narrows it to this academy and, for a student, to
    their own rows — so another student's sample is a 404 rather than a 403.
    Unchanged from Phase 6, including the deliberate absence of parents and
    sub-teachers: SaaS Phase 3 must not widen who can hear a minor's voice.
    """

    message = "Only the lead teacher or the student themselves can hear a sample."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role in {Role.LEAD, Role.STUDENT}
        )


class CanManageAcademyCurriculum(BasePermission):
    """Members read this academy's curriculum; its owner and admins change it.

    Both halves in one class because they are one decision per request, taken
    from the same already-resolved membership. Reading is open to every active
    member — a student needs the track list to submit a placement, and a teacher
    needs it to know what the academy teaches — while creating a track or a level
    is an act of running the business.

    ``view.caller_membership`` is ``None`` for a non-member, a suspended member and
    an anonymous caller alike, so no method passes for any of them. In practice
    ``IsOrganizationMember`` sits in front of this class and produces the clearer
    message for that case; the check is repeated here so the class is safe to use
    on its own.
    """

    message = (
        "Only an organization owner or administrator can change this academy's "
        "curriculum."
    )

    def has_permission(self, request, view):
        membership = view.caller_membership
        if membership is None:
            return False
        if request.method in SAFE_METHODS:
            return True
        return membership.role in CURRICULUM_MANAGER_ROLES


class IsAcademyCurriculumManager(BasePermission):
    """Owner and admin, for every method — reading included.

    The stricter sibling of ``CanManageAcademyCurriculum``, and it exists for one
    endpoint: the academy's teacher-track roster. A list of who has been entrusted
    with which subject names people, which puts it closer to the membership
    directory — owner/admin in Phase 1 — than to the syllabus every member may
    read. A teacher reads their own assignments from ``teachers/mine/``, so nothing
    is hidden from the person it is about.
    """

    message = (
        "Only an organization owner or administrator can see or configure this "
        "academy's teacher tracks."
    )

    def has_permission(self, request, view):
        membership = view.caller_membership
        return bool(membership and membership.role in CURRICULUM_MANAGER_ROLES)
