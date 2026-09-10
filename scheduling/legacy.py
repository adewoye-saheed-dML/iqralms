"""Assigning pre-SaaS availability to the academy that owns it.

``Availability.organization`` becomes non-nullable in SaaS Phase 4, so every row
that already exists needs an owner before the column can be enforced. This module
is what decides that, and it is a module rather than a block inside the migration
for one reason: a data migration that cannot be run in a test is a data migration
nobody has checked.

**The rule is "resolve or refuse", never "guess".** Assigning a teacher's
availability to the wrong tenant is not a bug that shows up as an error — it
shows up as one academy booking a teacher during hours intended for another — so
every ambiguous case raises with instructions instead of picking a plausible
answer:

.. code-block:: text

    no unowned availability        ->  nothing to do (a fresh install, and the
                                       test database, take this path)
    SAAS_LEGACY_SCHEDULING_ORGANIZATION set -> the academy it names
    exactly one organization       ->  that academy owns the legacy availability
    multiple organizations         ->  check teacher memberships / curriculum
                                       relationships: if unambiguous per teacher,
                                       assign accordingly; if ambiguous, refuse

No academy is ever *created* here. CLAUDE.md forbids inventing one, and the
reason is sound: an academy is a business with an owner, and a migration is not
in a position to decide who that is. The pre-SaaS product had an implicit single
academy, and naming it is a human act performed once, before this migration runs.

**Who the backfill admits, and why it must admit anybody.** Availability requires
that a teacher has an active membership in the organization (``active_membership()``).
Teachers with unowned availability who do not yet hold a membership in the target
organization are admitted as ``OrganizationRole.TEACHER`` with ``MembershipStatus.ACTIVE``.
Existing memberships are never altered — in particular, a suspended member is left
suspended, because reactivating one would be this migration silently granting
access an academy had taken away.
"""

import os

from organizations.models import MembershipStatus, OrganizationRole

#: Names the academy that owns pre-SaaS availability, by primary key or by slug.
#: Only consulted when the answer is not already unique.
LEGACY_ORGANIZATION_ENV = "SAAS_LEGACY_SCHEDULING_ORGANIZATION"

LEGACY_TEACHER_ROLE = OrganizationRole.TEACHER


class LegacySchedulingUnmapped(Exception):
    """Existing availability cannot be assigned to a determinable academy.

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
        raise LegacySchedulingUnmapped(
            f"{LEGACY_ORGANIZATION_ENV}={named!r} does not name an existing "
            "organization. Use the primary key or the slug of an academy that "
            "already exists."
        )
    return organization


def unowned_availability(Availability):
    """Availability windows written before scheduling had an owner."""
    return Availability.objects.filter(organization__isnull=True)


def admit_legacy_teachers(*, OrganizationMembership, organization, teachers):
    """Ensure teachers holding legacy availability have a membership.

    Idempotent, and never edits an existing membership — a suspended member
    stays suspended. Returns the number of memberships created.
    """
    if not teachers:
        return 0

    already = set(
        OrganizationMembership.objects.filter(
            organization=organization, user__in=teachers
        ).values_list("user_id", flat=True)
    )
    created = 0
    for teacher in teachers:
        teacher_id = getattr(teacher, "pk", teacher)
        if teacher_id in already:
            continue
        OrganizationMembership.objects.create(
            organization=organization,
            user_id=teacher_id,
            role=LEGACY_TEACHER_ROLE,
            status=MembershipStatus.ACTIVE,
        )
        created += 1
    return created


def resolve_organization_for_teacher(
    *,
    teacher,
    Organization,
    OrganizationMembership,
    TeacherTrack=None,
    TeacherProfile=None,
):
    """Determine the single organization for a teacher based on their memberships or tracks.

    Returns:
      - Organization instance if unambiguous
      - None if no organization candidates found
      - List of Organizations if multiple candidates found (ambiguous)
    """
    # 1. Existing memberships
    memberships = OrganizationMembership.objects.filter(user=teacher)
    org_ids = set(memberships.values_list("organization_id", flat=True))
    if len(org_ids) == 1:
        return Organization.objects.filter(pk=next(iter(org_ids))).first()
    if len(org_ids) > 1:
        return list(Organization.objects.filter(pk__in=org_ids).order_by("slug"))

    # 2. Migrated curriculum relationships (TeacherTrack)
    if TeacherTrack is not None:
        track_org_ids = set(
            TeacherTrack.objects.filter(membership__user=teacher)
            .values_list("membership__organization_id", flat=True)
        )
        if len(track_org_ids) == 1:
            return Organization.objects.filter(pk=next(iter(track_org_ids))).first()
        if len(track_org_ids) > 1:
            return list(Organization.objects.filter(pk__in=track_org_ids).order_by("slug"))

    # 3. Legacy specialties on tracks that now belong to an organization
    if TeacherProfile is not None:
        profile = TeacherProfile.objects.filter(user=teacher).first()
        if profile is not None:
            specialty_org_ids = set(
                profile.specialties.exclude(organization__isnull=True)
                .values_list("organization_id", flat=True)
            )
            if len(specialty_org_ids) == 1:
                return Organization.objects.filter(pk=next(iter(specialty_org_ids))).first()
            if len(specialty_org_ids) > 1:
                return list(Organization.objects.filter(pk__in=specialty_org_ids).order_by("slug"))

    return None


def choose_legacy_organization_assignments(
    *,
    Availability=None,
    Organization,
    OrganizationMembership,
    TeacherTrack=None,
    TeacherProfile=None,
    named=None,
    unowned_rows=None,
):
    """Map each unowned Availability row ID to its resolved Organization.

    Raises LegacySchedulingUnmapped if any row cannot be unambiguously mapped.
    ``unowned_rows`` can be provided directly by callers (e.g. tests); otherwise
    queried from ``Availability``.
    """
    if unowned_rows is None:
        if Availability is None:
            return {}
        unowned_qs = unowned_availability(Availability)
        if not unowned_qs.exists():
            return {}
        unowned_rows = list(unowned_qs.select_related("teacher"))
    elif not unowned_rows:
        return {}

    if named:
        target_org = _resolve_named(Organization, named)
        return {row.pk: target_org for row in unowned_rows}

    candidates = list(Organization.objects.order_by("pk")[:2])
    if not candidates:
        raise LegacySchedulingUnmapped(
            "There is existing availability but no organization to own it. "
            "SaaS Phase 4 makes Availability.organization required, and this "
            "migration will not invent an academy: create the academy first, "
            f"then re-run migrate with {LEGACY_ORGANIZATION_ENV}=<pk or slug>."
        )

    if len(candidates) == 1:
        target_org = candidates[0]
        return {row.pk: target_org for row in unowned_rows}

    # Multiple organizations: resolve per-teacher or refuse
    teachers = {row.teacher for row in unowned_rows}
    teacher_to_org = {}

    for teacher in teachers:
        resolved = resolve_organization_for_teacher(
            teacher=teacher,
            Organization=Organization,
            OrganizationMembership=OrganizationMembership,
            TeacherTrack=TeacherTrack,
            TeacherProfile=TeacherProfile,
        )
        if resolved is None:
            raise LegacySchedulingUnmapped(
                f"Teacher '{teacher.username}' has availability but no organization "
                "or membership to determine its owner. Re-run migrate with "
                f"{LEGACY_ORGANIZATION_ENV}=<pk or slug>."
            )
        if isinstance(resolved, list):
            slugs = ", ".join(f"'{o.slug}'" for o in resolved)
            raise LegacySchedulingUnmapped(
                f"Teacher '{teacher.username}' has availability and belongs to multiple "
                f"organizations ({slugs}). Cannot deterministically assign legacy "
                f"availability. Re-run migrate with {LEGACY_ORGANIZATION_ENV}=<pk or slug>."
            )
        teacher_to_org[teacher.pk] = resolved

    return {
        row.pk: teacher_to_org[getattr(row, "teacher_id", row.teacher.pk)]
        for row in unowned_rows
    }


def migrate_availability_to_organization(apps, schema_editor):
    """The migration entry point. A no-op on any database with no legacy availability.

    Follows curriculum/legacy.py pattern: exits cleanly if no unowned rows exist,
    otherwise deterministically assigns each unowned window to its owning organization
    and admits teachers who hold availability into that organization.
    """
    Availability = apps.get_model("scheduling", "Availability")
    Organization = apps.get_model("organizations", "Organization")
    OrganizationMembership = apps.get_model("organizations", "OrganizationMembership")
    TeacherTrack = apps.get_model("curriculum", "TeacherTrack")
    TeacherProfile = apps.get_model("accounts", "TeacherProfile")

    assignments = choose_legacy_organization_assignments(
        Availability=Availability,
        Organization=Organization,
        OrganizationMembership=OrganizationMembership,
        TeacherTrack=TeacherTrack,
        TeacherProfile=TeacherProfile,
        named=named_organization_setting(),
    )
    if not assignments:
        return

    # Group assignments by organization to perform efficient batched updates
    org_to_pks = {}
    for row_id, org in assignments.items():
        org_to_pks.setdefault(org, []).append(row_id)

    for org, row_pks in org_to_pks.items():
        teachers = list(
            Availability.objects.filter(pk__in=row_pks)
            .values_list("teacher", flat=True)
            .distinct()
        )
        Availability.objects.filter(pk__in=row_pks).update(organization=org)
        admit_legacy_teachers(
            OrganizationMembership=OrganizationMembership,
            organization=org,
            teachers=teachers,
        )
