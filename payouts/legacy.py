"""Audit and remediation for legacy pre-SaaS payout data.

SaaS Phase 7 establishes tenant boundaries across all payout operations:
- TeacherPayout -> Booking -> Level -> Track -> Organization
- TeacherPayout.cohort -> Track -> Organization (must match Booking)
- TeacherPayout.teacher -> User (must hold active membership in owning Organization)

Every payout resource belongs to the organization that owns the underlying
booking, and the teacher must hold active membership in that organization.

This module provides:
1. `audit_payout_data`: Audits existing TeacherPayout records for unanchored records,
   unadmitted/suspended teachers, cross-academy cohorts, and mismatch anomalies.
2. `remediate_legacy_payouts`: Non-destructively backfills missing memberships
   for teachers, ensures teacher configurations exist, and aligns mismatched cohort references.
3. `remediate_legacy_payouts_migration`: Entrypoint for Django data migrations.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from organizations.models import MembershipStatus, OrganizationRole

LEGACY_TEACHER_ROLE = OrganizationRole.TEACHER


@dataclass
class PayoutAuditReport:
    total_payouts: int = 0
    unanchored_payouts: List[int] = field(default_factory=list)
    unadmitted_teachers: List[Tuple[int, int, int]] = field(default_factory=list)
    suspended_teachers: List[Tuple[int, int, int]] = field(default_factory=list)
    unconfigured_teachers: List[Tuple[int, int, int]] = field(default_factory=list)
    teacher_mismatches: List[int] = field(default_factory=list)
    cohort_mismatches: List[int] = field(default_factory=list)
    cross_academy_cohorts: List[int] = field(default_factory=list)

    @property
    def has_anomalies(self) -> bool:
        return bool(
            self.unanchored_payouts
            or self.unadmitted_teachers
            or self.suspended_teachers
            or self.unconfigured_teachers
            or self.teacher_mismatches
            or self.cohort_mismatches
            or self.cross_academy_cohorts
        )


def audit_payout_data(
    *,
    TeacherPayout,
    OrganizationMembership,
    OrganizationTeacherConfiguration=None,
) -> PayoutAuditReport:
    """Audit all existing TeacherPayout records against SaaS Phase 7 invariants."""
    report = PayoutAuditReport()

    payouts = TeacherPayout.objects.all().select_related(
        "booking__level__track__organization",
        "cohort__level__track__organization",
    )
    report.total_payouts = payouts.count()

    for payout in payouts:
        booking = payout.booking
        if (
            not booking
            or not booking.level
            or not booking.level.track
            or not booking.level.track.organization
        ):
            report.unanchored_payouts.append(payout.pk)
            continue

        org = booking.level.track.organization

        # Check teacher matches booking teacher
        if payout.teacher_id != booking.teacher_id:
            report.teacher_mismatches.append(payout.pk)

        # Check cohort matches booking cohort
        if payout.cohort_id != booking.cohort_id:
            report.cohort_mismatches.append(payout.pk)

        # Check cohort organization if cohort present
        if (
            payout.cohort
            and payout.cohort.level
            and payout.cohort.level.track
            and payout.cohort.level.track.organization_id != org.pk
        ):
            report.cross_academy_cohorts.append(payout.pk)

        # Teacher membership check
        if payout.teacher_id:
            m = OrganizationMembership.objects.filter(
                organization=org, user_id=payout.teacher_id
            ).first()
            if m is None:
                report.unadmitted_teachers.append(
                    (payout.pk, payout.teacher_id, org.pk)
                )
            elif m.status == MembershipStatus.SUSPENDED:
                report.suspended_teachers.append(
                    (payout.pk, payout.teacher_id, org.pk)
                )
            elif OrganizationTeacherConfiguration is not None:
                if not OrganizationTeacherConfiguration.objects.filter(membership=m).exists():
                    report.unconfigured_teachers.append(
                        (payout.pk, payout.teacher_id, org.pk)
                    )

    return report


def remediate_legacy_payouts(
    *,
    TeacherPayout,
    OrganizationMembership,
    OrganizationTeacherConfiguration=None,
) -> Dict[str, int]:
    """Non-destructively remediate legacy payout data.

    1. Admits unadmitted teachers into the owning academy with role=TEACHER.
    2. Ensures active OrganizationTeacherConfiguration exists for admitted teachers.
    3. Repairs mismatched cohort foreign keys on TeacherPayout from booking.cohort.
    4. Never reactivates suspended memberships (respecting prior administrative actions).
    """
    summary = {
        "teachers_admitted": 0,
        "teachers_configured": 0,
        "cohorts_aligned": 0,
    }

    payouts = TeacherPayout.objects.all().select_related(
        "booking__level__track__organization"
    )

    teacher_org_map: Dict[int, Set[int]] = defaultdict(set)
    cohorts_to_update = []

    for payout in payouts:
        booking = payout.booking
        if (
            not booking
            or not booking.level
            or not booking.level.track
            or not booking.level.track.organization
        ):
            continue

        org_id = booking.level.track.organization_id

        if payout.cohort_id != booking.cohort_id:
            payout.cohort = booking.cohort
            cohorts_to_update.append(payout)

        if payout.teacher_id:
            teacher_org_map[org_id].add(payout.teacher_id)

    # Align cohorts
    if cohorts_to_update:
        TeacherPayout.objects.bulk_update(cohorts_to_update, ["cohort"])
        summary["cohorts_aligned"] = len(cohorts_to_update)

    # Backfill teachers
    for org_id, teacher_ids in teacher_org_map.items():
        existing = set(
            OrganizationMembership.objects.filter(
                organization_id=org_id, user_id__in=teacher_ids
            ).values_list("user_id", flat=True)
        )
        to_create = [
            OrganizationMembership(
                organization_id=org_id,
                user_id=uid,
                role=LEGACY_TEACHER_ROLE,
                status=MembershipStatus.ACTIVE,
            )
            for uid in (teacher_ids - existing)
        ]
        if to_create:
            OrganizationMembership.objects.bulk_create(to_create)
            summary["teachers_admitted"] += len(to_create)

        # Configure teacher rows
        if OrganizationTeacherConfiguration is not None:
            teacher_memberships = OrganizationMembership.objects.filter(
                organization_id=org_id, user_id__in=teacher_ids
            )
            existing_configs = set(
                OrganizationTeacherConfiguration.objects.filter(
                    membership__in=teacher_memberships
                ).values_list("membership_id", flat=True)
            )
            configs_to_create = [
                OrganizationTeacherConfiguration(
                    membership=m,
                    approved=True,
                    max_weekly_hours=20,
                )
                for m in teacher_memberships
                if m.pk not in existing_configs
            ]
            if configs_to_create:
                OrganizationTeacherConfiguration.objects.bulk_create(configs_to_create)
                summary["teachers_configured"] += len(configs_to_create)

    return summary


def remediate_legacy_payouts_migration(apps, schema_editor):
    """Migration entrypoint for non-destructively remediating legacy payout data."""
    TeacherPayout = apps.get_model("payouts", "TeacherPayout")
    OrganizationMembership = apps.get_model("organizations", "OrganizationMembership")
    OrganizationTeacherConfiguration = apps.get_model(
        "accounts", "OrganizationTeacherConfiguration"
    )

    remediate_legacy_payouts(
        TeacherPayout=TeacherPayout,
        OrganizationMembership=OrganizationMembership,
        OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
    )
