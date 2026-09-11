# Quran Academy SaaS Development Rules

## Accepted SaaS Phase 6 Decisions

- Assessment domain models (`AssessmentRubric`, `AssessmentCriterion`, `SessionAssessment`, `AssessmentScore`, `ProgressSnapshot`) derive organization ownership through `Track -> Organization` or `Booking -> Level -> Track -> Organization`. No redundant tenant columns added.
- Custom QuerySets provide `.in_organization(org)` and all assessment models expose `@property def organization`.
- `SessionAssessment.clean()` validates student and teacher active memberships in `self.organization`, teacher configuration, booking organization match, and lead reviewer membership.
- `AssessmentScore.clean()` validates criterion and assessment belong to the same organization.
- `ProgressSnapshot.clean()` validates active memberships for student and generator in `track.organization`.
- `save()` calls `full_clean()` across all assessment models.
- All 16 assessment endpoints are mounted under `/api/assessment/organizations/<organization_pk>/...` and guarded by `IsAuthenticated, IsOrganizationMember`.
- Scoped serializers validate related fields against `self.organization`.
- Legacy unscoped routes are retired completely.
- Privacy boundaries strictly enforced: families cannot view internal QC fields (flags, flag reasons, lead notes); teachers cannot view other teachers' submissions or lead review notes.
- Non-destructive legacy assessment audit and remediation implemented via migration `0002_remediate_legacy_assessment`.
- Adversarial tenant-isolation suite (`test_tenant_isolation.py`) verifies complete cross-academy isolation.

## SaaS Phase 6 Complete — Next Phase: SaaS Phase 7 Teacher Payout Tenancy

SaaS Phase 6 is complete. Its implementation, tests, migrations, PostgreSQL verification, tenant-isolation checks, API acceptance, OpenAPI verification, documentation, and commit have been completed.

SaaS Phase 7 is now the next phase.

Do not begin SaaS Phase 7 until explicitly requested.

## Working Session Discipline

- Work on one numbered task at a time.
- Start with an audit before changing schema or behavior.
- Use focused tests during development.
- Run the full gate at the phase boundary.
- Commit after each completed task or coherent implementation unit.
- Keep phase documentation split into a core file and one file per numbered task.
- Do not introduce unrelated frontend, payment, billing, or infrastructure changes during this phase.

## Phase Completion Rule

A phase is complete only when implementation, tests, fresh migrations, PostgreSQL verification, tenant-isolation checks, manual/API acceptance, OpenAPI verification, documentation, and git commit are complete.
