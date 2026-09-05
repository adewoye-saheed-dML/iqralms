"""Assigning pre-SaaS curriculum to the academy that owns it.

``Track.organization`` becomes non-nullable in SaaS Phase 3, so every row that
already exists needs an owner before the column can be enforced. This module is
what decides that, and it is a module rather than a block inside the migration for
one reason: a data migration that cannot be run in a test is a data migration
nobody has checked.

**The rule is "resolve or refuse", never "guess".** Assigning a tenant's
curriculum to the wrong tenant is not a bug that shows up as an error — it shows
up as one academy quietly reading another's syllabus and student placements — so
every ambiguous case raises with instructions instead of picking a plausible
answer:

.. code-block:: text

    no unowned tracks              ->  nothing to do (a fresh install, and the
                                       test database, take this path)
    exactly one organization       ->  that academy owns the legacy curriculum
    SAAS_LEGACY_CURRICULUM_ORGANIZATION set  ->  the academy it names
    anything else                  ->  refuse, and say what to set

No academy is ever *created* here. CLAUDE.md forbids inventing one, and the
reason is sound: an academy is a business with an owner, and a migration is not
in a position to decide who that is. The pre-SaaS product had exactly one implicit
academy, and naming it is a human act performed once, before this migration runs.

**Who the backfill admits, and why it must admit anybody.** A placement is
readable in an academy only when its student is an active member there
(``PlacementResult.in_organization``), so leaving legacy students outside the
academy would make their existing placements invisible — the phase spec requires
that old placement data stay readable. So the users who *already hold* legacy
curriculum data are admitted: the students with placements, the leads who reviewed
them, and the teachers with recorded specialties. Nobody else, and no existing
membership is altered — in particular a suspended member is left suspended, because
reactivating one would be this migration silently granting access an academy had
taken away.
"""

import os

from accounts.models import TEACHER_ROLES
from organizations.models import MembershipStatus, OrganizationRole

#: Names the academy that owns pre-SaaS curriculum, by primary key or by slug.
#: Only consulted when the answer is not already unique.
LEGACY_ORGANIZATION_ENV = "SAAS_LEGACY_CURRICULUM_ORGANIZATION"

#: The organization role a legacy participant is admitted with. ``OrganizationRole``
#: has no ``student`` or ``parent`` value — a gap tech-debt.md already records — so
#: a legacy student or parent lands on ``staff``, which carries no authority over
#: memberships, curriculum or teaching terms. A teaching account gets ``teacher``.
LEGACY_TEACHER_ROLE = OrganizationRole.TEACHER
LEGACY_MEMBER_ROLE = OrganizationRole.STAFF


class LegacyCurriculumUnmapped(Exception):
    """Existing curriculum cannot be assigned to a determinable academy.

    Raised *from inside the migration*, so ``migrate`` stops with a message that
    says what to do rather than committing a guess. The spec's "stop and ask"
    case, expressed in code.
    """


def named_organization_setting():
    """The academy named in the environment, or ``None``."""
    value = os.environ.get(LEGACY_ORGANIZATION_ENV)
    return value.strip() if value and value.strip() else None


def _resolve_named(Organization, named):
    """The organization named by pk or slug. Raises when it does not exist."""
    organization = None
    if named.isdigit():
        organization = Organization.objects.filter(pk=int(named)).first()
    if organization is None:
        organization = Organization.objects.filter(slug=named).first()
    if organization is None:
        raise LegacyCurriculumUnmapped(
            f"{LEGACY_ORGANIZATION_ENV}={named!r} does not name an existing "
            "organization. Use the primary key or the slug of an academy that "
            "already exists."
        )
    return organization


def choose_legacy_organization(*, Organization, named=None, curriculum_needs_owner=True):
    """The academy that owns pre-SaaS curriculum, or ``None`` when there is none.

    ``curriculum_needs_owner`` is the caller's answer to "are there unowned tracks
    at all", passed in rather than queried here so the decision itself can be
    tested against every shape of the organization table.
    """
    if not curriculum_needs_owner:
        return None
    if named:
        return _resolve_named(Organization, named)

    candidates = list(Organization.objects.order_by("pk")[:2])
    if len(candidates) == 1:
        # Unambiguous: there is one academy, so the curriculum that predates
        # multi-tenancy is that academy's.
        return candidates[0]
    if not candidates:
        raise LegacyCurriculumUnmapped(
            "There is existing curriculum but no organization to own it. "
            "SaaS Phase 3 makes Track.organization required, and this migration "
            "will not invent an academy: create the academy first (POST "
            "/api/organizations/, or the admin), then re-run migrate with "
            f"{LEGACY_ORGANIZATION_ENV}=<pk or slug>."
        )
    raise LegacyCurriculumUnmapped(
        "There is existing curriculum and more than one organization, so its "
        "owner is not determinable. Re-run migrate with "
        f"{LEGACY_ORGANIZATION_ENV}=<pk or slug> naming the academy whose "
        "curriculum this is."
    )


def unowned_tracks(Track):
    """Tracks written before curriculum had an owner."""
    return Track.objects.filter(organization__isnull=True)


def assign_tracks(*, Track, organization):
    """Give every unowned track to ``organization``. Returns how many moved.

    A queryset update: primary keys are preserved, levels and placements follow
    their track without being touched, and nothing is deleted or recreated.
    """
    return unowned_tracks(Track).update(organization=organization)


def _legacy_participants(*, Track, PlacementResult, TeacherProfile, organization):
    """The users who already hold curriculum data in ``organization``.

    Returns ``{user_id: is_teaching_account}``. Students with placements, the
    leads stamped on those reviews, and teachers with a recorded specialty — and
    deliberately nobody else, because "every existing user" would be this
    migration deciding an academy's roster for it.
    """
    track_ids = set(
        Track.objects.filter(organization=organization).values_list("pk", flat=True)
    )
    if not track_ids:
        return {}

    participants = {}
    placements = PlacementResult.objects.filter(track_id__in=track_ids).values_list(
        "student_id", "reviewed_by_id"
    )
    for student_id, reviewer_id in placements:
        participants.setdefault(student_id, False)
        if reviewer_id is not None:
            participants[reviewer_id] = True

    specialists = TeacherProfile.objects.filter(
        specialties__pk__in=track_ids
    ).values_list("user_id", "user__role")
    for user_id, role in specialists:
        participants[user_id] = role in TEACHER_ROLES

    return participants


def admit_legacy_participants(
    *, Track, PlacementResult, TeacherProfile, OrganizationMembership, organization
):
    """Give the academy's existing curriculum participants a membership.

    Idempotent, and it never edits a membership that is already there — a
    suspended member stays suspended. Returns the number of memberships created.
    """
    participants = _legacy_participants(
        Track=Track,
        PlacementResult=PlacementResult,
        TeacherProfile=TeacherProfile,
        organization=organization,
    )
    if not participants:
        return 0

    already = set(
        OrganizationMembership.objects.filter(
            organization=organization, user_id__in=participants
        ).values_list("user_id", flat=True)
    )
    created = 0
    for user_id, teaches in participants.items():
        if user_id in already:
            continue
        # One row at a time rather than bulk_create: the model's uniqueness rules
        # are what make a re-run safe, and bulk_create is CLAUDE.md's named example
        # of bypassing invariants.
        OrganizationMembership.objects.create(
            organization=organization,
            user_id=user_id,
            role=LEGACY_TEACHER_ROLE if teaches else LEGACY_MEMBER_ROLE,
            status=MembershipStatus.ACTIVE,
        )
        created += 1
    return created


def backfill_teacher_tracks(
    *, Track, TeacherProfile, TeacherTrack, OrganizationMembership, organization
):
    """Turn legacy global specialties into academy-scoped eligibility.

    Only for tracks this academy now owns, and only for teachers who hold an
    *active* membership here. Nothing is copied into any other academy — a global
    specialty was never a statement about a second tenant — and the legacy
    ``TeacherProfile.specialties`` rows are left exactly where they are, because
    scheduling still reads them until SaaS Phase 4.

    Returns ``(created, skipped)``: how many assignments were written, and the
    ``(username, track slug)`` pairs that could not be mapped because the teacher
    is not an active member of this academy. Those are reported rather than forced,
    per the spec's instruction to document an unmappable specialty instead of
    guessing at one.
    """
    memberships = {
        user_id: membership_id
        for membership_id, user_id in OrganizationMembership.objects.filter(
            organization=organization, status=MembershipStatus.ACTIVE
        ).values_list("pk", "user_id")
    }
    owned = set(
        Track.objects.filter(organization=organization).values_list("pk", flat=True)
    )
    existing = set(
        TeacherTrack.objects.filter(track_id__in=owned).values_list(
            "membership_id", "track_id"
        )
    )

    created = 0
    skipped = []
    pairs = TeacherProfile.objects.filter(specialties__pk__in=owned).values_list(
        "user_id", "user__username", "user__role", "specialties__pk", "specialties__slug"
    )
    for user_id, username, role, track_id, slug in pairs:
        membership_id = memberships.get(user_id)
        if membership_id is None or role not in TEACHER_ROLES:
            skipped.append((username, slug))
            continue
        if (membership_id, track_id) in existing:
            continue
        TeacherTrack.objects.create(
            membership_id=membership_id, track_id=track_id, active=True
        )
        existing.add((membership_id, track_id))
        created += 1
    return created, skipped


def migrate_curriculum_to_organization(apps, schema_editor):
    """The migration entry point. A no-op on any database with no legacy tracks.

    Written so the *normal* deployment path — a fresh database, and the test
    database — runs it as a single ``EXISTS`` query and moves on, while an existing
    single-academy installation is migrated in full or stopped with an explanation.
    """
    Track = apps.get_model("curriculum", "Track")
    TeacherTrack = apps.get_model("curriculum", "TeacherTrack")
    PlacementResult = apps.get_model("curriculum", "PlacementResult")
    Organization = apps.get_model("organizations", "Organization")
    OrganizationMembership = apps.get_model("organizations", "OrganizationMembership")
    TeacherProfile = apps.get_model("accounts", "TeacherProfile")

    organization = choose_legacy_organization(
        Organization=Organization,
        named=named_organization_setting(),
        curriculum_needs_owner=unowned_tracks(Track).exists(),
    )
    if organization is None:
        return

    assign_tracks(Track=Track, organization=organization)
    admit_legacy_participants(
        Track=Track,
        PlacementResult=PlacementResult,
        TeacherProfile=TeacherProfile,
        OrganizationMembership=OrganizationMembership,
        organization=organization,
    )
    _, skipped = backfill_teacher_tracks(
        Track=Track,
        TeacherProfile=TeacherProfile,
        TeacherTrack=TeacherTrack,
        OrganizationMembership=OrganizationMembership,
        organization=organization,
    )
    if skipped:
        # Not fatal: the legacy relation is still in place and still read by
        # scheduling, so nothing is lost — but an unmapped specialty is exactly
        # what the spec asks to be told about rather than have papered over.
        listed = ", ".join(f"{username}/{slug}" for username, slug in sorted(skipped))
        print(
            "\n  curriculum: could not map these legacy teacher specialties to "
            f"{organization.slug} because the teacher is not an active member "
            f"there: {listed}. TeacherProfile.specialties still holds them; "
            "assign the tracks through the academy's teacher-tracks endpoint "
            "once the membership exists."
        )
