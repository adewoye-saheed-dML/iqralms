# SaaS Phase 7 — Teacher Payout Tenancy

## Status

COMPLETE

## Objective

Make the existing teacher payout and statement domain fully academy-scoped without changing the underlying payout business rules already implemented in the repository.

The repository already has a functioning `payouts` application from the original Phase 8 work. SaaS Phase 7 must add the multi-tenant boundary required by the SaaS roadmap:

```text
Platform
  └── Organization / Academy
       └── Payout records
            ├── Teacher payouts
            ├── Statements
            └── Generation / finalization
```

This phase is about **tenancy, authorization, ownership, privacy, migration, and API scoping**.

It is not a second payout-product phase.

## Current repository baseline

The current `main` branch is at:

```text
9bde46c920dc18ef8f60645ff7a2960789bd04b4
feat(saas): implement Phase 6 assessment tenancy and complete phase gate
```

The current repository already contains:

```text
organizations/
accounts/
curriculum/
scheduling/
pricing/
assessment/
payouts/
specs/saas/
```

The payout domain currently contains:

```text
payouts/models.py
payouts/services.py
payouts/serializers.py
payouts/permissions.py
payouts/views.py
payouts/urls.py
payouts/migrations/
payouts/tests/
```

The current payout API is still mounted globally under:

```text
/api/payouts/
```

The existing payout permission layer is based primarily on global user/teacher roles. That is insufficient for SaaS tenancy.

## Existing payout rules that must be preserved

The original payout implementation establishes these rules:

### Eligibility

Only completed eligible teaching sessions can produce payouts.

These do not produce payouts:

```text
scheduled
cancelled
no_show
```

Bookings without a payout-eligible teacher do not produce payouts.

### Calculation

The current Phase 8 calculation remains the source of truth:

```text
payout amount = eligible session duration × applicable teacher payout rate
```

Teacher payout remains independent of:

- family pricing;
- pricing agreements;
- assessment scores;
- routing outcome.

### Historical values

A payout stores its calculation inputs/results.

Changing a teacher's current payout rate must not rewrite historical finalized payouts.

### Lifecycle

The existing lifecycle remains:

```text
generated → finalized
```

Finalized payouts remain immutable.

### Generation

Generation is bounded by a period and is intended to be repeat-safe.

SaaS Phase 7 must not weaken that idempotency while adding tenant scoping.

### Statements

Statements remain computed from payout records.

Do not create a duplicate financial fact store solely for tenancy.

## Preferred ownership model

First prove the ownership path from the current code.

The preferred design is to derive payout organization through authoritative relationships already present in scheduling/curriculum rather than adding a second organization column without need.

The expected ownership chain should be equivalent to:

```text
TeacherPayout
  → Booking / Cohort
      → Level / Track or other authoritative academic owner
          → Organization
```

The exact path must be confirmed from the current repository before schema changes.

The implementation must answer these questions deterministically:

1. Which academy owns the booking?
2. Which academy owns the cohort, if present?
3. Does the payout teacher belong to that academy?
4. Can the payout reference a teacher from another academy?
5. Can one payout have ambiguous academy ownership?

If the repository proves that a direct `TeacherPayout.organization` field is necessary to maintain an unambiguous, efficient tenant boundary, document that reason before adding it. Do not duplicate ownership merely because it is convenient.

## Core tenant invariants

### 1. One payout, one academy

Every payout must have exactly one provable organization owner.

No payout may be globally visible.

### 2. Booking ownership

A payout booking must belong to the same academy as the payout.

### 3. Cohort ownership

When `TeacherPayout.cohort` is present, the cohort must belong to the same academy as:

- the payout;
- the booking;
- the teacher membership.

### 4. Teacher membership

The payout teacher must have an active membership in the owning academy when the payout is created or otherwise validated.

Historical rows must not become inaccessible merely because a later membership status changes; distinguish historical ownership from current access.

### 5. Lead/admin authority

An academy owner/admin may manage payout records only inside that academy.

Do not rely only on:

```python
request.user.role == lead
```

The organization context and active membership must also be checked.

### 6. Teacher visibility

A teacher may view only:

```text
their own payouts
+
their own statements
+
inside an academy where they are an active teacher member
```

A teacher must not view another teacher's payouts.

### 7. Student/parent denial

Students and parents have no payout access.

This must be enforced server-side.

### 8. Object-id isolation

Known or guessed payout IDs from another academy must still produce an appropriate denial/not-found behavior and must never disclose the other academy's record.

### 9. Generation isolation

A generation request for Academy A must query only Academy A's eligible bookings.

Academy B bookings must never be considered.

### 10. Cross-tenant relation rejection

The application must reject combinations such as:

```text
Academy A booking + Academy B teacher
Academy A booking + Academy B cohort
Academy A payout + Academy B teacher
Academy A payout + Academy B booking
Academy A statement + Academy B payout rows
```

## Tasks

| Task | Purpose | Status |
|---|---|---|
| 7.1 | Payout ownership and data-flow audit | COMPLETE |
| 7.2 | Payout model tenant integrity | COMPLETE |
| 7.3 | Payout service/generation tenant scoping | COMPLETE |
| 7.4 | Payout API route and queryset tenancy | COMPLETE |
| 7.5 | Payout permissions and privacy | COMPLETE |
| 7.6 | Legacy payout data and migration/remediation | COMPLETE |
| 7.7 | Adversarial tenant-isolation tests | COMPLETE |
| 7.8 | Acceptance, OpenAPI, and documentation | COMPLETE |

---

# 7.1 Payout ownership and data-flow audit

Before changing the schema, inspect:

```text
payouts/models.py
payouts/services.py
payouts/serializers.py
payouts/permissions.py
payouts/views.py
payouts/urls.py
payouts/tests/
scheduling/models.py
curriculum/models.py
accounts/models.py
organizations/models.py
```

Map:

```text
TeacherPayout
    ↓
Booking
    ↓
Level / Track / Cohort ownership
    ↓
Organization
```

Also inspect all code paths that create, list, finalize, or summarize payouts.

Document the final proven ownership path.

### Acceptance

Task 7.1 is complete when:

- every payout has a deterministic organization owner;
- the teacher relation can be checked against that organization;
- the cohort relation can be checked against that organization;
- statement totals can be scoped to the same organization;
- generation can be scoped without changing payout calculation.

---

# 7.2 Payout model tenant integrity

Update `TeacherPayout` so its organization ownership is explicit through the repository's proven ownership path.

Add organization-aware queryset support consistent with prior SaaS phases, for example:

```python
TeacherPayout.objects.in_organization(organization)
```

Do not blindly add a duplicate `organization` foreign key if the ownership path is already deterministic.

Add model/service validation for:

- payout organization;
- booking organization;
- teacher membership;
- cohort organization;
- teacher/booking match;
- cohort/booking match.

Keep the original payout invariants intact.

Do not change:

- payout amount formula;
- rate source;
- status lifecycle;
- finalization semantics;
- currency;
- period boundary rules.

### Historical rows

If legacy payout rows predate organization-aware scheduling data, determine whether the academy can be assigned deterministically.

Do not assign historical financial records using a guessed or arbitrary academy.

If every legacy payout maps cleanly to one academy, migrate/remediate them deterministically.

If some rows cannot be assigned safely, document and isolate them before permitting them into tenant-visible financial workflows.

### Acceptance

Task 7.2 is complete when direct ORM writes cannot create a payout that crosses academy boundaries.

---

# 7.3 Payout service and generation tenancy

Refactor payout services so every tenant-aware operation receives an explicit organization context.

Generation should conceptually become:

```text
organization
+ period_start
+ period_end
+ optional teacher filter
→ eligible bookings inside organization
→ missing payout records inside organization
→ generated result
```

Do not allow a generation function to discover its tenant from:

- a global lead user;
- a global teacher role;
- an unscoped booking queryset.

The organization must constrain the source bookings before payout records are created.

A repeated generation for the same organization and period must remain idempotent.

Statements must calculate totals only from payout records owned by the same organization and requested teacher.

### Acceptance

Task 7.3 is complete when:

- Academy A generation never touches Academy B bookings;
- generation remains repeat-safe;
- statement totals contain only tenant-owned payouts;
- existing payout calculations are unchanged.

---

# 7.4 Payout API route and queryset tenancy

Move the payout API to an explicit organization-scoped contract consistent with SaaS Phases 4–6.

Preferred shape:

```text
GET  /api/payouts/organizations/<organization_pk>/mine/
GET  /api/payouts/organizations/<organization_pk>/mine/?start=&end=
GET  /api/payouts/organizations/<organization_pk>/statements/mine/
GET  /api/payouts/organizations/<organization_pk>/lead/
GET  /api/payouts/organizations/<organization_pk>/statements/
POST /api/payouts/organizations/<organization_pk>/generate/
POST /api/payouts/organizations/<organization_pk>/<payout_id>/finalize/
```

Exact naming may follow established repository conventions.

### Queryset rules

Every payout queryset must begin from the requested organization boundary.

Examples:

```text
academy payout list
→ payout queryset
→ organization filter
→ permission check
→ teacher/self filter where needed
```

Never do:

```text
all payouts
→ filter in serializer
```

Never trust a client-supplied organization id without membership verification.

### Legacy routes

The old unscoped routes under:

```text
/api/payouts/
```

must not remain a bypass.

Retire them or replace them with organization-scoped equivalents according to the repository's existing migration strategy.

### Acceptance

Task 7.4 is complete when every payout endpoint requires and verifies organization context.

---

# 7.5 Payout permissions and privacy

Create or adapt payout permissions so they combine:

```text
authenticated user
+
active organization membership
+
organization role
+
teacher ownership where required
```

The permission layer and queryset layer must both participate.

### Owner/admin

May:

- list academy payouts;
- generate academy payouts;
- finalize academy payouts;
- view academy teacher statements.

May not:

- read another academy's payout records;
- bypass organization membership.

### Teacher/sub-teacher

May:

- view own payout records;
- view own statements;
- inspect enough session information to reconcile their own totals.

May not:

- view another teacher's payout;
- view another academy's payout;
- generate payouts;
- finalize payouts;
- edit finalized payouts;
- manage payout rates through a payout endpoint unless an existing approved teacher-management workflow explicitly grants that ability.

### Student

No payout access.

### Parent

No payout access.

### Suspended member

No current payout access.

### Acceptance

Task 7.5 is complete when permission and queryset tests prove both role control and tenant control.

---

# 7.6 Legacy payout data and migration/remediation

Audit existing payout rows before marking the phase complete.

Determine:

```text
total legacy payouts
payouts with deterministic academy ownership
payouts with ambiguous ownership
payouts with missing related tenant records
```

Use a migration or explicit remediation mechanism where appropriate.

Do not silently delete financial history.

Do not fabricate organization ownership.

A migration must be safe to run against a fresh PostgreSQL database.

If no schema migration is required because ownership is derived entirely through existing tenant-scoped relationships, document that explicitly and add regression checks proving it.

### Acceptance

Task 7.6 is complete when legacy payout data is either deterministically tenant-resolvable or deliberately isolated/documented.

---

# 7.7 Adversarial tenant-isolation tests

Create a focused payout tenant-isolation suite.

At minimum test:

## Academy separation

```text
Academy A payout
Academy B payout
Academy A lead
Academy B lead
```

Verify each lead sees only their own academy.

## Teacher separation

```text
Academy A Teacher 1
Academy A Teacher 2
Academy B Teacher 1
```

Verify:

- Teacher 1 sees own payout only;
- Teacher 1 cannot access Teacher 2 payout;
- Teacher 1 cannot access Academy B payout.

## Known-id attacks

Use a known payout primary key from another academy and attempt:

```text
GET
finalize
```

Verify no cross-tenant disclosure or mutation is possible.

## Generation isolation

Create completed bookings in two academies.

Generate payouts for Academy A.

Verify:

```text
Academy A payout count increases correctly
Academy B payout count does not change
```

## Cross-tenant relationships

Attempt to create invalid combinations:

```text
A booking + B teacher
A booking + B cohort
A payout + B booking
A payout + B teacher
```

All must be rejected.

## Membership state

Test:

```text
active membership
suspended membership
non-member
```

Only the active member may enter tenant APIs.

## Role state

Test:

```text
owner
admin
teacher
student
parent
```

Verify each capability against the final role matrix.

## Historical immutability

Finalize a payout, change the current teacher rate, and verify:

```text
historical rate unchanged
historical amount unchanged
```

## Repeat generation

Run the same bounded generation twice.

Verify:

```text
no duplicate payout
```

### Regression requirement

All existing payout, scheduling, pricing, assessment, and routing tests must continue to pass.

---

# 7.8 Acceptance, OpenAPI, and documentation

Verify the complete payout journey through the API:

```text
Owner/Admin
  → select academy
  → inspect payout records
  → generate bounded period
  → inspect generated payout
  → finalize payout
  → regenerate same period
  → confirm no duplicate

Teacher
  → select academy
  → open own payout history
  → open own statement
  → reconcile totals

Security
  → attempt another teacher payout
  → attempt another academy payout
  → attempt payout as student
  → attempt payout as parent
  → attempt payout as suspended member
```

Verify OpenAPI documents:

- organization parameter;
- authentication;
- organization membership requirement;
- role requirement;
- teacher self-scope;
- error responses;
- payout generation payload;
- finalization endpoint.

Update:

```text
learnings.md
tech-debt.md
```

when the phase creates a deliberate architectural decision or limitation.

---

# API contract

The final API must make tenant context explicit.

## Teacher self-service

```text
GET /api/payouts/organizations/<organization_pk>/mine/
GET /api/payouts/organizations/<organization_pk>/mine/?start=&end=
GET /api/payouts/organizations/<organization_pk>/statements/mine/?start=&end=
```

## Lead/admin

```text
GET  /api/payouts/organizations/<organization_pk>/lead/
GET  /api/payouts/organizations/<organization_pk>/statements/?teacher_id=&start=&end=
POST /api/payouts/organizations/<organization_pk>/generate/
POST /api/payouts/organizations/<organization_pk>/<payout_id>/finalize/
```

The exact final URL names may follow existing repository route conventions, but the organization boundary is mandatory.

## Error behaviour

Use consistent API errors for:

- unauthenticated;
- non-member;
- suspended membership;
- insufficient organization role;
- unknown payout inside another academy;
- invalid cross-tenant relationship.

Do not disclose the existence of another academy's financial record unnecessarily.

---

# Data invariants

The implementation must enforce all of the following:

1. A payout has one academy owner.
2. Booking and payout academy agree.
3. Cohort and payout academy agree when a cohort exists.
4. Teacher and booking agree.
5. Teacher has an active membership in the payout academy when creating a new payout.
6. Generation only queries bookings inside the requested academy.
7. Teacher self-service only queries the requesting teacher's own payouts.
8. Owner/admin management is academy-scoped.
9. Student has no payout access.
10. Parent has no payout access.
11. Suspended members cannot use payout APIs.
12. Known payout IDs cannot cross tenant boundaries.
13. One payout per eligible booking remains true.
14. Cohort payout uniqueness remains true.
15. Finalized payouts remain immutable.
16. Rate snapshots remain immutable.
17. Family pricing remains independent.
18. Assessment remains independent.
19. Statement totals equal the sum of the tenant-scoped payout rows used to construct the statement.
20. Period membership remains based on the stored booking start time and existing UTC rules.

---

# Out of scope

Do not implement:

- payment gateways;
- Paystack/Stripe;
- bank transfers;
- automatic payout execution;
- invoices;
- receipts;
- tax/accounting;
- accounting exports;
- currency conversion;
- payout corrections/reversals;
- assessment-based compensation;
- teacher ranking;
- automatic rate changes;
- background payout jobs;
- WhatsApp/email payout notifications;
- frontend payout UI;
- separate database per academy.

Those belong to later product decisions/phases.

---

# Phase gate

Run:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Run the gate against PostgreSQL.

Also verify:

```text
two-academy manual acceptance
teacher privacy
owner/admin isolation
student/parent denial
suspended-membership denial
known-id isolation
generation isolation
statement isolation
OpenAPI route/permission documentation
```

---

# Definition of done

SaaS Phase 7 is complete only when:

1. Every payout has deterministic academy ownership.
2. Payout model/service invariants reject cross-academy relationships.
3. Payout generation is tenant-scoped.
4. Teacher self-service is tenant-scoped and self-only.
5. Owner/admin management is tenant-scoped.
6. Student and parent payout access is denied.
7. Suspended memberships are denied.
8. Legacy payout routes cannot bypass tenancy.
9. Historical payout values remain unchanged.
10. Repeated generation remains idempotent.
11. Existing payout business rules remain unchanged.
12. Legacy data has been deterministically migrated/remediated or explicitly isolated.
13. Adversarial tenant-isolation tests pass.
14. Existing regression suites pass.
15. PostgreSQL migration and verification pass.
16. OpenAPI reflects the final tenant-aware API.
17. Manual two-academy acceptance passes.
18. `learnings.md` and `tech-debt.md` are updated where needed.
19. A coherent git commit marks the completed phase.
20. The repository is ready to move to the next explicitly approved SaaS phase.

---

# Stop and ask instead of guessing

Stop before implementation if:

- the payout organization cannot be determined unambiguously;
- legacy payout rows cannot be mapped safely;
- the current scheduling/curriculum tenant model conflicts with payout ownership;
- the organization finance-role policy is unclear;
- a new rate model is proposed;
- finalized payout correction/reversal is requested;
- payment execution is requested;
- currency or financial calculation rules are proposed to change.

Routine tenancy work may proceed without clarification when it follows the rules already established by SaaS Phases 1–6 and the existing Phase 8 payout specification.
