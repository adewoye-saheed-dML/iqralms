# Quran Academy SaaS — Phase 10 Claude Instructions

## Repository

Repository: `adewoye-saheed-dML/quran_acad`

Target branch: `main`

## Current phase

Implement **SaaS Phase 10 — Bulk Import**.

Read:

`specs/saas/SaaS Phase 10 — Bulk Import.md`

before changing code. Treat it as the source of truth for Phase 10.

The live repository currently marks SaaS Phase 9 — Video Provider Abstraction complete and explicitly identifies SaaS Phase 10 as the next phase.

---

## Primary objective

Build a tenant-safe CSV/XLSX bulk-import backend for academy onboarding.

The intended flow is:

```text
upload
→ parse
→ map
→ validate
→ preview/report
→ commit
→ summary
```

Do not make file upload itself mutate academy data.

---

## Important existing architecture

`User` is a global identity.

`OrganizationMembership` defines the user's relationship with a specific academy.

`ParentLink` represents the global parent/child relationship.

Do not add `User.organization`.

Do not create a second organization-role or membership system.

Current organization membership states are only:

```text
active
suspended
```

Do not invent `pending` or `invited` states in this phase.

---

## Supported import kinds

Start with explicit import kinds:

```text
teachers
students
parents
```

Do not build a generic arbitrary-model importer.

Define the exact canonical columns from the current models during Task 10.1.

Do not invent fields merely because a spreadsheet might contain them.

---

## Non-negotiable rules

1. Every import belongs to exactly one academy.
2. The academy comes from authenticated organization context, not a spreadsheet column.
3. Never trust a spreadsheet `organization_id`, `academy_id`, or tenant identifier.
4. Validate before mutating domain records.
5. Existing-user matching must be deterministic.
6. Prefer normalized email as the initial matching key.
7. Never guess identity from names.
8. Do not silently reactivate suspended memberships.
9. Do not silently change conflicting academy roles.
10. Do not accept passwords in imports.
11. Never expose secrets in import reports.
12. Do not create a parent link from ambiguous data.
13. Do not duplicate users or memberships on repeat imports.
14. Re-check tenant authorization during commit.
15. Keep provider/video/notification logic out of the importer.
16. Do not build the frontend spreadsheet editor in this phase.

---

## Required implementation sequence

### Task 10.1 — Audit

Inspect:

```text
accounts/
organizations/
curriculum/
scheduling/
assessment/
pricing/
payouts/
notifications/
```

Identify the authoritative fields needed for:

- teacher creation/membership;
- student creation/membership;
- parent creation/membership;
- parent-child linking.

Do not design import fields from assumptions.

### Task 10.2 — ImportJob

Create the tenant-scoped import job.

Store enough metadata to audit:

```text
organization
created_by
kind
file metadata
status
mapping
row counts
validation result
import result
timestamps
```

Keep sensitive source data retention minimal.

### Task 10.3 — Parsing and mapping

Support:

```text
CSV
XLSX
```

Reject unsupported or oversized files.

Implement explicit header mapping and supported aliases.

Reject:

- duplicate mappings;
- forbidden authority fields;
- unsupported canonical fields.

### Task 10.4 — Validation

Validation must be mutation-free.

Produce structured row errors:

```text
row
field
code
message
```

At minimum validate:

- required values;
- email;
- timezone;
- dates;
- duplicates;
- conflicting rows;
- existing-user ambiguity;
- parent references;
- academy role/configuration conflicts.

### Task 10.5 — Matching and membership

For existing users:

```text
normalized email
→ resolve User
→ resolve Academy membership
→ create/reuse when safe
```

Never match by name alone.

If role or membership status conflicts:

```text
do not overwrite automatically
report the conflict
```

### Task 10.6 — Parent links

Create `ParentLink` only when:

```text
parent resolved
student resolved
both are valid academy participants
relationship is explicit
```

Never infer family relationships from names or spreadsheet ordering.

### Task 10.7 — Commit

Use transactional behaviour appropriate to the repository.

Default semantics should be:

- create missing users;
- reuse matching users;
- create missing memberships;
- do not overwrite unrelated profile values;
- report differences that were not applied.

Ensure the same import cannot be committed twice.

### Task 10.8 — Tests

Add:

- parser tests;
- mapping tests;
- validation tests;
- tenant-isolation tests;
- duplicate/repeat tests;
- membership conflict tests;
- parent-link tests;
- concurrency tests;
- regression tests.

### Task 10.9 — API

Prefer an organization-scoped workflow such as:

```text
POST /api/imports/organizations/<organization_pk>/validate/
POST /api/imports/organizations/<organization_pk>/<import_id>/commit/
```

Use existing routing and permission conventions.

### Task 10.10 — Gate

Run:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Run against PostgreSQL.

Perform manual two-academy acceptance.

Update OpenAPI.

Update documentation only for actual decisions.

---

## Security expectations

Treat every spreadsheet as untrusted input.

Consider:

- file size;
- row count;
- cell size;
- malformed XLSX;
- duplicate headers;
- hidden columns;
- formula-containing values;
- invalid encodings;
- arbitrary organization identifiers;
- passwords or secrets embedded in data.

Do not create credentials from spreadsheet values.

---

## Tenant isolation tests must prove

```text
Academy A cannot read Academy B import jobs.
Academy A cannot validate Academy B jobs.
Academy A cannot commit Academy B jobs.
Known B job id cannot bypass permissions.
Spreadsheet cannot switch tenant through a column.
Parent links cannot cross academies.
Teacher memberships cannot cross academies.
Suspended users cannot execute import operations.
```

---

## Idempotency and concurrency

Prove:

```text
same file twice
→ no duplicate users

same file twice
→ no duplicate memberships
```

Also prove:

```text
same import committed concurrently
→ no duplicate records
```

Use the database as the concurrency authority. Do not rely on an in-memory lock.

---

## Do not expand scope

Do not add:

- invitation delivery;
- WhatsApp/email/Telegram onboarding;
- new membership statuses;
- payment import;
- payout migration;
- assessment-history migration;
- frontend spreadsheet editor;
- AI column mapping;
- arbitrary model import.

---

## Stop instead of guessing

Stop and inspect the repository if:

- the current model cannot safely represent an imported field;
- identity matching is ambiguous;
- a parent/child relationship cannot be proven;
- an import requires a new membership state;
- import semantics would change authorization;
- account activation is required;
- partial-import behaviour becomes unavoidable without an explicit rule.

Do not invent a data model solely to make the import work.

---

## Phase completion standard

Do not mark Phase 10 complete because a CSV can be parsed.

It is complete only when:

- CSV and XLSX are supported for the documented import kinds;
- validation happens before mutation;
- row errors are structured;
- academy tenancy is enforced;
- existing users are matched deterministically;
- duplicate memberships are prevented;
- parent relationships are safe;
- repeat imports are controlled;
- concurrent commits are safe;
- permissions use existing membership/role infrastructure;
- PostgreSQL checks pass;
- regression tests pass;
- OpenAPI is updated;
- documentation is updated;
- a coherent phase-completion commit is created.
