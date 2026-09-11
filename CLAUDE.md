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

## Current Phase — SaaS Phase 5 Pricing Tenancy

The current phase makes the existing pricing agreement domain tenant-safe. Do not introduce payments, billing, invoices, subscriptions, wallets, or teacher payouts in this phase.

Specification: `specs/saas/SaaS Phase 5 — Pricing Tenancy.md`

Task files:
- `specs/saas/phase-5/00-core.md`
- `specs/saas/phase-5/01-pricing-ownership-audit.md`
- `specs/saas/phase-5/02-pricing-model-integrity.md`
- `specs/saas/phase-5/03-pricing-api-tenancy.md`
- `specs/saas/phase-5/04-pricing-permissions-and-privacy.md`
- `specs/saas/phase-5/05-pricing-tenant-isolation-tests.md`
- `specs/saas/phase-5/06-legacy-data-and-migrations.md`
- `specs/saas/phase-5/07-acceptance-and-documentation.md`

Do not start SaaS Phase 6 until Phase 5 implementation, tests, migrations, tenant isolation, manual/API acceptance, OpenAPI verification, documentation, and commit are complete.

## Working Session Discipline

- Work on one numbered task at a time.
- Start with an audit before changing schema or behavior.
- Use focused tests during development; run the full gate at the phase boundary.
- Commit after each completed task or coherent implementation unit.
- Keep phase documentation split into a core file and one file per numbered task.

## Phase Completion Rule

A phase is complete only when implementation, tests, fresh migrations, PostgreSQL verification, tenant-isolation checks, manual/API acceptance, OpenAPI verification, documentation, and git commit are complete.
