"""Audit and remediation for legacy pre-SaaS pricing agreements.

SaaS Phase 5 establishes tenant boundaries on PricingAgreement via its Level
(level.track.organization). Every pricing agreement belongs to the organization
of its level, and both its student and its approver must hold active memberships
in that organization. Furthermore, at most one agreement may be active for a
given (student, level) pair.

This module provides:
1. `audit_pricing_agreements`: Audits existing PricingAgreement rows for missing
   relations, unadmitted or suspended participants, and duplicate active agreements.
2. `remediate_legacy_pricing`: Non-destructively backfills missing memberships for
   students and approvers holding legacy agreements, and deactivates older duplicate
   active agreements so the newest agreement remains the sole active one.
3. `remediate_legacy_pricing_migration`: Entrypoint for Django data migrations.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from organizations.models import MembershipStatus, OrganizationRole

LEGACY_STUDENT_ROLE = OrganizationRole.STAFF
LEGACY_APPROVER_ROLE = OrganizationRole.TEACHER


@dataclass
class AuditReport:
    total_agreements: int = 0
    clean_agreements: int = 0
    missing_relations: List[int] = field(default_factory=list)
    unadmitted_students: List[Tuple[int, int, int]] = field(default_factory=list)
    suspended_students: List[Tuple[int, int, int]] = field(default_factory=list)
    unadmitted_approvers: List[Tuple[int, int, int]] = field(default_factory=list)
    suspended_approvers: List[Tuple[int, int, int]] = field(default_factory=list)
    duplicate_active_groups: Dict[Tuple[int, int], List[int]] = field(
        default_factory=lambda: defaultdict(list)
    )

    @property
    def has_anomalies(self) -> bool:
        return bool(
            self.missing_relations
            or self.unadmitted_students
            or self.suspended_students
            or self.unadmitted_approvers
            or self.suspended_approvers
            or self.duplicate_active_groups
        )


def audit_pricing_agreements(
    *, PricingAgreement, OrganizationMembership
) -> AuditReport:
    """Audit all existing PricingAgreement rows against Phase 5 invariants."""
    report = AuditReport()
    agreements = (
        PricingAgreement.objects.all()
        .select_related("level__track__organization")
        .order_by("student_id", "level_id", "-pk")
    )
    report.total_agreements = agreements.count()

    active_by_pair: Dict[Tuple[int, int], List[int]] = defaultdict(list)

    for agreement in agreements:
        if (
            not agreement.level
            or not agreement.level.track
            or not agreement.level.track.organization
            or not agreement.student_id
            or not agreement.approved_by_id
        ):
            report.missing_relations.append(agreement.pk)
            continue

        org = agreement.level.track.organization

        if agreement.active:
            active_by_pair[(agreement.student_id, agreement.level_id)].append(
                agreement.pk
            )

        # Check student membership
        student_membership = OrganizationMembership.objects.filter(
            organization=org, user_id=agreement.student_id
        ).first()
        if student_membership is None:
            report.unadmitted_students.append(
                (agreement.pk, agreement.student_id, org.pk)
            )
        elif student_membership.status == MembershipStatus.SUSPENDED:
            report.suspended_students.append(
                (agreement.pk, agreement.student_id, org.pk)
            )

        # Check approver membership
        approver_membership = OrganizationMembership.objects.filter(
            organization=org, user_id=agreement.approved_by_id
        ).first()
        if approver_membership is None:
            report.unadmitted_approvers.append(
                (agreement.pk, agreement.approved_by_id, org.pk)
            )
        elif approver_membership.status == MembershipStatus.SUSPENDED:
            report.suspended_approvers.append(
                (agreement.pk, agreement.approved_by_id, org.pk)
            )

    for pair, ids in active_by_pair.items():
        if len(ids) > 1:
            report.duplicate_active_groups[pair] = ids

    report.clean_agreements = (
        report.total_agreements
        - len(report.missing_relations)
        - len(report.unadmitted_students)
        - len(report.suspended_students)
        - len(report.unadmitted_approvers)
        - len(report.suspended_approvers)
        - sum(len(ids) - 1 for ids in report.duplicate_active_groups.values())
    )
    return report


def remediate_legacy_pricing(
    *, PricingAgreement, OrganizationMembership
) -> Dict[str, int]:
    """Non-destructively remediate legacy pricing agreements.

    1. Admits unadmitted students into the agreement's organization with role=STAFF.
    2. Admits unadmitted approvers into the agreement's organization with role=TEACHER.
    3. Resolves multiple active agreements for the same (student, level) pair by
       leaving the most recently created row active and deactivating earlier rows.
    4. Never reactivates suspended memberships (respecting prior administrative action).
    """
    summary = {
        "students_admitted": 0,
        "approvers_admitted": 0,
        "duplicate_agreements_deactivated": 0,
    }

    agreements = (
        PricingAgreement.objects.all()
        .select_related("level__track__organization")
        .order_by("student_id", "level_id", "-pk")
    )

    # 1. Backfill memberships
    student_org_map: Dict[int, Set[int]] = defaultdict(set)
    approver_org_map: Dict[int, Set[int]] = defaultdict(set)
    active_by_pair: Dict[Tuple[int, int], List[Any]] = defaultdict(list)

    for agreement in agreements:
        if (
            not agreement.level
            or not agreement.level.track
            or not agreement.level.track.organization
            or not agreement.student_id
            or not agreement.approved_by_id
        ):
            continue

        org_id = agreement.level.track.organization_id
        student_org_map[org_id].add(agreement.student_id)
        approver_org_map[org_id].add(agreement.approved_by_id)

        if agreement.active:
            active_by_pair[(agreement.student_id, agreement.level_id)].append(agreement)

    # Backfill students
    for org_id, student_ids in student_org_map.items():
        existing = set(
            OrganizationMembership.objects.filter(
                organization_id=org_id, user_id__in=student_ids
            ).values_list("user_id", flat=True)
        )
        to_create = [
            OrganizationMembership(
                organization_id=org_id,
                user_id=uid,
                role=LEGACY_STUDENT_ROLE,
                status=MembershipStatus.ACTIVE,
            )
            for uid in (student_ids - existing)
        ]
        if to_create:
            OrganizationMembership.objects.bulk_create(to_create)
            summary["students_admitted"] += len(to_create)

    # Backfill approvers
    for org_id, approver_ids in approver_org_map.items():
        existing = set(
            OrganizationMembership.objects.filter(
                organization_id=org_id, user_id__in=approver_ids
            ).values_list("user_id", flat=True)
        )
        to_create = [
            OrganizationMembership(
                organization_id=org_id,
                user_id=uid,
                role=LEGACY_APPROVER_ROLE,
                status=MembershipStatus.ACTIVE,
            )
            for uid in (approver_ids - existing)
        ]
        if to_create:
            OrganizationMembership.objects.bulk_create(to_create)
            summary["approvers_admitted"] += len(to_create)

    # Deactivate older duplicate active agreements
    to_deactivate = []
    for pair, rows in active_by_pair.items():
        if len(rows) > 1:
            for older_agreement in rows[1:]:
                older_agreement.active = False
                to_deactivate.append(older_agreement)

    if to_deactivate:
        PricingAgreement.objects.bulk_update(to_deactivate, ["active"])
        summary["duplicate_agreements_deactivated"] = len(to_deactivate)

    return summary


def remediate_legacy_pricing_migration(apps, schema_editor):
    """Migration entrypoint for non-destructively remediating legacy pricing data."""
    PricingAgreement = apps.get_model("pricing", "PricingAgreement")
    OrganizationMembership = apps.get_model(
        "organizations", "OrganizationMembership"
    )
    remediate_legacy_pricing(
        PricingAgreement=PricingAgreement,
        OrganizationMembership=OrganizationMembership,
    )
