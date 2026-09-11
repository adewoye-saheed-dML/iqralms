# Quran Academy SaaS Development Rules

## Accepted SaaS Phase 4 Decisions

- `Availability.organization` is an explicit FK.
- `Booking`, `Cohort`, and `TeacherWaitlist` derive organization through `level.track.organization`.
- `OrganizationTeacherConfiguration` handles tenant capacity.
- Global physical overlap/locking remains global; tenant capacity is organization-scoped.
- Scheduling requires active membership, teacher configuration, and curriculum eligibility.
- Scheduling APIs are organization-scoped; legacy unscoped scheduling routes are retired/blocked.
- Serializers and permissions enforce tenant isolation server-side.
- Adversarial tenant-isolation tests are required.

## Accepted SaaS Phase 5 Decisions

- `PricingAgreement` derives organization ownership through `level.track.organization`. No redundant `organization` column is added.
- `PricingAgreement.clean()` validates that both the student and the approver hold active memberships in `level.track.organization`.
- `PricingAgreement.save()` calls `full_clean()` to guarantee invariant enforcement across ORM, admin, and API writes.
- Pricing APIs are mounted under `/api/pricing/organizations/<organization_pk>/agreements/` and `/api/pricing/organizations/<organization_pk>/agreements/mine/`.
- Legacy unscoped routes (`/api/pricing/agreements/` and `.../mine/`) are retired and return 404 to eliminate tenant bypass.
- Write serializers declare foreign relations with `Model.objects.none()` and scope them only in `get_fields()` with request context, failing closed and allowing clean OpenAPI schema inspection.
- Legacy data is remediated non-destructively by backfilling active memberships for unadmitted historical students/approvers and deactivating older duplicate active agreements.
- Multi-academy boundary isolation is verified through an adversarial test suite (`test_tenant_isolation.py`).

## SaaS Phase 5 Complete — Next Phase: SaaS Phase 6 Assessment Tenancy

SaaS Phase 5 is COMPLETE. All 7 tasks, 98 pricing tests, 1180 multi-app tests, migrations, tenant isolation tests, and OpenAPI schema validation pass against PostgreSQL.

Do not start SaaS Phase 6 until Phase 6 specifications and development tasks are planned and approved.

## Working Session Discipline

- Work on one numbered task at a time.
- Start with an audit before changing schema or behavior.
- Use focused tests during development; run the full gate at the phase boundary.
- Commit after each completed task or coherent implementation unit.
- Keep phase documentation split into a core file and one file per numbered task.

## Phase Completion Rule

A phase is complete only when implementation, tests, fresh migrations, PostgreSQL verification, tenant-isolation checks, manual/API acceptance, OpenAPI verification, documentation, and git commit are complete.
