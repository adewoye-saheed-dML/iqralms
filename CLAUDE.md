# Quran Academy SaaS Development Rules

## Repository baseline

Repository: `adewoye-saheed-dML/quran_acad`

Target branch: `main`

Current audited commit before SaaS Phase 7:
`9bde46c920dc18ef8f60645ff7a2960789bd04b4`

Current completed SaaS phases:
- SaaS Phase 1 — Organization Foundation
- SaaS Phase 2 — Accounts Tenancy
- SaaS Phase 3 — Curriculum Tenancy
- SaaS Phase 4 — Scheduling Tenancy
- SaaS Phase 5 — Pricing Tenancy
- SaaS Phase 6 — Assessment Tenancy

The next implementation phase is **SaaS Phase 7 — Teacher Payout Tenancy**.

The repository already contains the non-SaaS payout implementation from Phase 8. SaaS Phase 7 is not a new payout-product phase. It is the tenant-safety migration of that existing payout domain.

## Accepted SaaS Phase 6 decisions

- Assessment resources derive organization ownership through the existing curriculum/booking relationships rather than adding unnecessary duplicate tenant fields.
- Assessment querysets expose organization scoping.
- Assessment serializers and model validation enforce same-organization relationships.
- Assessment APIs are mounted under organization-scoped routes.
- Assessment permissions require authenticated active organization membership.
- Legacy unscoped assessment routes are retired.
- Student, parent, teacher, and lead assessment visibility is organization-scoped.
- Historical assessment snapshots remain auditable.
- Adversarial cross-tenant assessment tests are required.
- SaaS Phase 6 is complete only after tests, PostgreSQL verification, migration checks, tenant-isolation checks, API acceptance, OpenAPI verification, documentation, and commit.

## SaaS Phase 7 objective

Make the existing `payouts` domain tenant-safe.

The existing payout business rules must remain intact:

- Completed eligible teaching sessions can generate payouts.
- Cancelled, no-show, and scheduled bookings do not generate payouts.
- One eligible booking produces at most one payout.
- Group/cohort sessions remain paid according to the existing Phase 8 rules.
- The teacher payout rate remains independent of family pricing.
- Assessment data does not change payout amounts.
- Payouts preserve the actual rate and amount used at generation time.
- Finalized payouts remain immutable.
- Generation remains repeat-safe/idempotent.
- Teacher self-service remains limited to the teacher's own payout history/statements.
- Lead payout management remains restricted to the academy that owns the records.

Do not redesign these rules during SaaS Phase 7 unless the current code is proven inconsistent with them.

## Current payout architecture to preserve

The existing payout app contains:

- `payouts/models.py`
- `payouts/services.py`
- `payouts/serializers.py`
- `payouts/permissions.py`
- `payouts/views.py`
- `payouts/urls.py`
- payout migrations
- payout automated tests

The existing API is currently mounted under `/api/payouts/` and uses role-only permissions. SaaS Phase 7 must replace the global financial boundary with organization-aware access.

The preferred ownership model is to derive payout organization from the authoritative scheduled academic object chain already present in the repository. Do not add a redundant `organization` foreign key to `TeacherPayout` unless the audit proves that ownership cannot be determined safely from its booking/cohort relationships.

The organization boundary must never be inferred from the caller's `User.role`.

## Non-negotiable tenant invariants

1. Every `TeacherPayout` has exactly one unambiguous owning academy.
2. A payout's teacher must be an active member of that academy when the payout is created or otherwise validated for active tenancy.
3. A payout's booking must belong to the same academy as the payout.
4. A payout's cohort, when present, must belong to the same academy as the payout.
5. A lead/admin may only read and manage payout records belonging to the selected academy where they have sufficient organization authority.
6. A teacher may only read payout records belonging to an academy where they have an active teacher membership.
7. A teacher may only see their own payout records inside that academy.
8. A teacher must never retrieve another academy's payout by guessing an object ID.
9. A payout-generation request must operate only over bookings belonging to the requested academy.
10. A payout-generation request must never create a payout in one academy from a booking in another academy.
11. A payout cannot reference a teacher, booking, cohort, or other tenant-owned object from another academy.
12. Parent and student accounts have no payout access.
13. Finalized payout history remains immutable after tenancy changes.
14. Existing family pricing and assessment isolation rules remain unchanged.
15. Legacy unscoped payout routes must not remain as a tenant bypass.
16. Querysets, serializers, model/service validation, permissions, and tests must all enforce the boundary. Serializer filtering alone is insufficient.

## Organization role rules

Use the existing organization membership system as the tenant authority.

Do not invent a second organization-role system.

Use:
- `OrganizationMembership`
- `OrganizationRole`
- `MembershipStatus`
- `active_membership()`

Organization-level payout authority should follow the existing SaaS model. Owner/admin roles are allowed to manage academy-wide payout records unless the current codebase documents a stricter finance-specific policy.

The old global `User.role == lead` rule must not be treated as the complete tenant permission boundary.

A user can be a teacher in one organization and have a different relationship in another organization. The payout API must evaluate the organization membership for the requested academy.

## API direction

Prefer the same organization-scoped route pattern already established by SaaS Phases 4–6.

The final tenant-aware payout surface should follow this shape unless an audit of existing routing establishes a repository-wide convention that is materially better:

```text
GET  /api/payouts/organizations/<organization_pk>/mine/
GET  /api/payouts/organizations/<organization_pk>/mine/?start=&end=
GET  /api/payouts/organizations/<organization_pk>/statements/mine/
GET  /api/payouts/organizations/<organization_pk>/lead/
GET  /api/payouts/organizations/<organization_pk>/statements/
POST /api/payouts/organizations/<organization_pk>/generate/
POST /api/payouts/organizations/<organization_pk>/<payout_id>/finalize/
```

Exact route names may follow the patterns already used by the current repository, but every endpoint must have an explicit organization context.

Do not trust a client-supplied organization id merely because it is syntactically valid. Verify active membership and role on the server before returning or changing any payout.

Legacy `/api/payouts/...` routes must be retired or otherwise made incapable of bypassing tenant checks.

## Implementation discipline

- Start each task with an audit of the current code.
- Do not duplicate existing tenant logic.
- Reuse existing organization helpers and conventions.
- Prefer model relationships over redundant tenant fields.
- Preserve existing payout calculations and historical behavior.
- Do not introduce billing, payment gateways, bank transfers, invoices, tax, accounting exports, or frontend work.
- Do not introduce a second payout-rate source.
- Do not use `bulk_create()` if it bypasses payout invariants.
- Keep organization filtering server-side.
- Use PostgreSQL for the phase gate.
- Run focused tests while implementing.
- Run the full gate before marking the phase complete.
- Commit each coherent task or implementation unit.

## Required phase gate

At minimum:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

The gate must be executed against PostgreSQL.

Also perform manual two-academy acceptance and OpenAPI verification.

## Required tenant-isolation tests

The test suite must prove at least:

- Academy A lead/admin cannot list Academy B payouts.
- Academy A teacher cannot read Academy B payout ids even when ids are known.
- Academy A teacher cannot read another teacher's payout in Academy A.
- Academy A lead/admin cannot finalize an Academy B payout.
- Generation for Academy A never considers Academy B bookings.
- A booking from Academy A cannot create a payout whose teacher or cohort belongs to Academy B.
- A payout with mismatched booking/teacher/cohort organization is rejected.
- Suspended memberships cannot use payout endpoints.
- Student accounts cannot use payout endpoints.
- Parent accounts cannot use payout endpoints.
- Re-running generation remains idempotent within the correct academy.
- Finalized payouts remain immutable.
- Historical payout amount/rate remains unchanged after current rate changes.
- Existing pricing and assessment tenant-isolation regressions still pass.

## Documentation requirements

Before completion update the project documentation where appropriate:

- `learnings.md` with decisions that affect future phases.
- `tech-debt.md` only for deliberate, accepted limitations.
- SaaS Phase 7 core spec status/task state.
- OpenAPI/API documentation where route or permission behavior changed.

Do not claim the phase is complete until the implementation, migrations, tests, PostgreSQL verification, tenant-isolation checks, manual acceptance, OpenAPI verification, documentation, and commit are complete.

## Stop instead of guessing

Stop before implementation when:

- payout ownership cannot be derived unambiguously from existing relationships;
- cohort-to-organization ownership is contradictory;
- the existing payout-rate source is no longer sufficient;
- payout correction/reversal behavior is requested;
- payment execution or accounting behavior is requested;
- organization-level financial authority conflicts with an existing repository decision;
- historical payout rows cannot be safely assigned to an academy without a deterministic rule.

Routine queryset, permission, serializer, service, route, migration, and test changes may proceed when the rule is already explicit.

## Phase boundary

SaaS Phase 7 is the tenant-safety phase for the already-existing teacher payout domain.

Do not turn this phase into:
- a new payment system;
- subscription billing;
- bank transfer automation;
- payout corrections/reversals;
- a frontend payout dashboard;
- notification delivery;
- multi-currency accounting;
- a redesign of the Phase 8 payout calculation.

After SaaS Phase 7 is accepted, stop and wait for the next explicitly requested phase.
