"""Audit and remediation for legacy pre-SaaS assessment data.

SaaS Phase 6 establishes tenant boundaries across all assessment models:
- AssessmentRubric -> Track -> Organization
- AssessmentCriterion -> AssessmentRubric -> Track -> Organization
- SessionAssessment -> Booking -> Level -> Track -> Organization
- AssessmentScore -> SessionAssessment -> Booking -> Level -> Track -> Organization
- ProgressSnapshot -> Track -> Organization

Every assessment resource belongs to the organization that owns the underlying
curriculum or booking, and all participants (students, teachers, reviewers,
generators) must hold active memberships in that organization.

This module provides:
1. `audit_assessment_data`: Audits existing assessment tables for missing relations,
   unanchored records, unadmitted/suspended participants, and cross-academy inconsistencies.
2. `remediate_legacy_assessment`: Non-destructively backfills missing memberships
   for students, teachers, reviewers, and generators, ensures teacher configurations exist,
   and repairs mismatched track foreign keys on assessments.
3. `remediate_legacy_assessment_migration`: Entrypoint for Django data migrations.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from organizations.models import MembershipStatus, OrganizationRole

LEGACY_STUDENT_ROLE = OrganizationRole.STAFF
LEGACY_TEACHER_ROLE = OrganizationRole.TEACHER
LEGACY_LEAD_ROLE = OrganizationRole.ADMIN


@dataclass
class AssessmentAuditReport:
    total_rubrics: int = 0
    total_assessments: int = 0
    total_scores: int = 0
    total_snapshots: int = 0

    unanchored_rubrics: List[int] = field(default_factory=list)
    unanchored_assessments: List[int] = field(default_factory=list)
    unanchored_snapshots: List[int] = field(default_factory=list)

    unadmitted_students: List[Tuple[int, int, int]] = field(default_factory=list)
    suspended_students: List[Tuple[int, int, int]] = field(default_factory=list)

    unadmitted_teachers: List[Tuple[int, int, int]] = field(default_factory=list)
    unconfigured_teachers: List[Tuple[int, int, int]] = field(default_factory=list)
    suspended_teachers: List[Tuple[int, int, int]] = field(default_factory=list)

    unadmitted_reviewers: List[Tuple[int, int, int]] = field(default_factory=list)
    unadmitted_generators: List[Tuple[int, int, int]] = field(default_factory=list)

    mismatched_tracks: List[int] = field(default_factory=list)
    cross_org_scores: List[int] = field(default_factory=list)

    @property
    def has_anomalies(self) -> bool:
        return bool(
            self.unanchored_rubrics
            or self.unanchored_assessments
            or self.unanchored_snapshots
            or self.unadmitted_students
            or self.suspended_students
            or self.unadmitted_teachers
            or self.unconfigured_teachers
            or self.suspended_teachers
            or self.unadmitted_reviewers
            or self.unadmitted_generators
            or self.mismatched_tracks
            or self.cross_org_scores
        )


def audit_assessment_data(
    *,
    AssessmentRubric,
    SessionAssessment,
    AssessmentScore,
    ProgressSnapshot,
    OrganizationMembership,
    OrganizationTeacherConfiguration=None,
) -> AssessmentAuditReport:
    """Audit all existing assessment records against SaaS Phase 6 invariants."""
    report = AssessmentAuditReport()

    # 1. Rubrics
    rubrics = AssessmentRubric.objects.all().select_related("track__organization")
    report.total_rubrics = rubrics.count()
    for rubric in rubrics:
        if not rubric.track or not rubric.track.organization:
            report.unanchored_rubrics.append(rubric.pk)

    # 2. SessionAssessments
    assessments = (
        SessionAssessment.objects.all()
        .select_related("booking__level__track__organization", "track")
    )
    report.total_assessments = assessments.count()

    for assessment in assessments:
        booking = assessment.booking
        if (
            not booking
            or not booking.level
            or not booking.level.track
            or not booking.level.track.organization
        ):
            report.unanchored_assessments.append(assessment.pk)
            continue

        org = booking.level.track.organization

        # Check track alignment
        if assessment.track_id != booking.level.track_id:
            report.mismatched_tracks.append(assessment.pk)

        # Student membership check
        if assessment.student_id:
            m = OrganizationMembership.objects.filter(
                organization=org, user_id=assessment.student_id
            ).first()
            if m is None:
                report.unadmitted_students.append(
                    (assessment.pk, assessment.student_id, org.pk)
                )
            elif m.status == MembershipStatus.SUSPENDED:
                report.suspended_students.append(
                    (assessment.pk, assessment.student_id, org.pk)
                )

        # Teacher membership check
        if assessment.assessed_by_id:
            m = OrganizationMembership.objects.filter(
                organization=org, user_id=assessment.assessed_by_id
            ).first()
            if m is None:
                report.unadmitted_teachers.append(
                    (assessment.pk, assessment.assessed_by_id, org.pk)
                )
            elif m.status == MembershipStatus.SUSPENDED:
                report.suspended_teachers.append(
                    (assessment.pk, assessment.assessed_by_id, org.pk)
                )
            elif OrganizationTeacherConfiguration is not None:
                if not OrganizationTeacherConfiguration.objects.filter(membership=m).exists():
                    report.unconfigured_teachers.append(
                        (assessment.pk, assessment.assessed_by_id, org.pk)
                    )

        # Lead reviewer membership check
        if assessment.lead_reviewed_by_id:
            m = OrganizationMembership.objects.filter(
                organization=org, user_id=assessment.lead_reviewed_by_id
            ).first()
            if m is None or m.status == MembershipStatus.SUSPENDED:
                report.unadmitted_reviewers.append(
                    (assessment.pk, assessment.lead_reviewed_by_id, org.pk)
                )

    # 3. AssessmentScores
    scores = AssessmentScore.objects.all().select_related(
        "assessment__booking__level__track__organization",
        "criterion__rubric__track__organization",
    )
    report.total_scores = scores.count()
    for score in scores:
        assessment_org_id = None
        criterion_org_id = None
        if (
            score.assessment
            and score.assessment.booking
            and score.assessment.booking.level
            and score.assessment.booking.level.track
        ):
            assessment_org_id = score.assessment.booking.level.track.organization_id

        if (
            score.criterion
            and score.criterion.rubric
            and score.criterion.rubric.track
        ):
            criterion_org_id = score.criterion.rubric.track.organization_id

        if assessment_org_id and criterion_org_id and assessment_org_id != criterion_org_id:
            report.cross_org_scores.append(score.pk)

    # 4. ProgressSnapshots
    snapshots = ProgressSnapshot.objects.all().select_related("track__organization")
    report.total_snapshots = snapshots.count()
    for snapshot in snapshots:
        if not snapshot.track or not snapshot.track.organization:
            report.unanchored_snapshots.append(snapshot.pk)
            continue

        org = snapshot.track.organization

        if snapshot.student_id:
            m = OrganizationMembership.objects.filter(
                organization=org, user_id=snapshot.student_id
            ).first()
            if m is None:
                report.unadmitted_students.append(
                    (snapshot.pk, snapshot.student_id, org.pk)
                )
            elif m.status == MembershipStatus.SUSPENDED:
                report.suspended_students.append(
                    (snapshot.pk, snapshot.student_id, org.pk)
                )

        if snapshot.generated_by_id:
            m = OrganizationMembership.objects.filter(
                organization=org, user_id=snapshot.generated_by_id
            ).first()
            if m is None or m.status == MembershipStatus.SUSPENDED:
                report.unadmitted_generators.append(
                    (snapshot.pk, snapshot.generated_by_id, org.pk)
                )

    return report


def remediate_legacy_assessment(
    *,
    SessionAssessment,
    ProgressSnapshot,
    OrganizationMembership,
    OrganizationTeacherConfiguration=None,
) -> Dict[str, int]:
    """Non-destructively remediate legacy assessment data.

    1. Admits unadmitted students into the owning academy with role=STAFF.
    2. Admits unadmitted teachers into the owning academy with role=TEACHER,
       and creates active OrganizationTeacherConfiguration.
    3. Admits unadmitted lead reviewers / snapshot generators with role=ADMIN.
    4. Repairs mismatched or null track foreign keys on SessionAssessment from booking.level.track.
    5. Never reactivates suspended memberships (respecting prior administrative actions).
    """
    summary = {
        "students_admitted": 0,
        "teachers_admitted": 0,
        "teachers_configured": 0,
        "reviewers_admitted": 0,
        "generators_admitted": 0,
        "tracks_repaired": 0,
    }

    # 1. Process SessionAssessments
    assessments = (
        SessionAssessment.objects.all()
        .select_related("booking__level__track__organization")
    )

    student_org_map: Dict[int, Set[int]] = defaultdict(set)
    teacher_org_map: Dict[int, Set[int]] = defaultdict(set)
    reviewer_org_map: Dict[int, Set[int]] = defaultdict(set)
    tracks_to_update = []

    for assessment in assessments:
        booking = assessment.booking
        if (
            not booking
            or not booking.level
            or not booking.level.track
            or not booking.level.track.organization
        ):
            continue

        org_id = booking.level.track.organization_id

        if assessment.track_id != booking.level.track_id:
            assessment.track = booking.level.track
            tracks_to_update.append(assessment)

        if assessment.student_id:
            student_org_map[org_id].add(assessment.student_id)
        if assessment.assessed_by_id:
            teacher_org_map[org_id].add(assessment.assessed_by_id)
        if assessment.lead_reviewed_by_id:
            reviewer_org_map[org_id].add(assessment.lead_reviewed_by_id)

    # 2. Process ProgressSnapshots
    snapshots = ProgressSnapshot.objects.all().select_related("track__organization")
    generator_org_map: Dict[int, Set[int]] = defaultdict(set)

    for snapshot in snapshots:
        if not snapshot.track or not snapshot.track.organization:
            continue
        org_id = snapshot.track.organization_id
        if snapshot.student_id:
            student_org_map[org_id].add(snapshot.student_id)
        if snapshot.generated_by_id:
            generator_org_map[org_id].add(snapshot.generated_by_id)

    # --- Execute repairs ---

    # Repair tracks
    if tracks_to_update:
        SessionAssessment.objects.bulk_update(tracks_to_update, ["track"])
        summary["tracks_repaired"] = len(tracks_to_update)

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

    # Backfill reviewers
    for org_id, reviewer_ids in reviewer_org_map.items():
        existing = set(
            OrganizationMembership.objects.filter(
                organization_id=org_id, user_id__in=reviewer_ids
            ).values_list("user_id", flat=True)
        )
        to_create = [
            OrganizationMembership(
                organization_id=org_id,
                user_id=uid,
                role=LEGACY_LEAD_ROLE,
                status=MembershipStatus.ACTIVE,
            )
            for uid in (reviewer_ids - existing)
        ]
        if to_create:
            OrganizationMembership.objects.bulk_create(to_create)
            summary["reviewers_admitted"] += len(to_create)

    # Backfill generators
    for org_id, generator_ids in generator_org_map.items():
        existing = set(
            OrganizationMembership.objects.filter(
                organization_id=org_id, user_id__in=generator_ids
            ).values_list("user_id", flat=True)
        )
        to_create = [
            OrganizationMembership(
                organization_id=org_id,
                user_id=uid,
                role=LEGACY_LEAD_ROLE,
                status=MembershipStatus.ACTIVE,
            )
            for uid in (generator_ids - existing)
        ]
        if to_create:
            OrganizationMembership.objects.bulk_create(to_create)
            summary["generators_admitted"] += len(to_create)

    return summary


def remediate_legacy_assessment_migration(apps, schema_editor):
    """Migration entrypoint for non-destructively remediating legacy assessment data."""
    SessionAssessment = apps.get_model("assessment", "SessionAssessment")
    ProgressSnapshot = apps.get_model("assessment", "ProgressSnapshot")
    OrganizationMembership = apps.get_model("organizations", "OrganizationMembership")
    OrganizationTeacherConfiguration = apps.get_model(
        "accounts", "OrganizationTeacherConfiguration"
    )

    remediate_legacy_assessment(
        SessionAssessment=SessionAssessment,
        ProgressSnapshot=ProgressSnapshot,
        OrganizationMembership=OrganizationMembership,
        OrganizationTeacherConfiguration=OrganizationTeacherConfiguration,
    )
