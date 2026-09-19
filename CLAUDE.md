# Quran Academy SaaS Development Rules — Phase 12

Repository: `adewoye-saheed-dML/iqralms`
Target branch: `main`

Verified current head:

`81c79dbceb50858c64a2c94eb36c52e3ed957e9a`

Latest commit:

`feat(Audit): completed`

Previous Phase 11 implementation:

`eee409a10f5e56e36375d282616c6764dbf67320`

## Completed phases

1. Organization Foundation
2. Accounts Tenancy
3. Curriculum Tenancy
4. Scheduling Tenancy
5. Pricing Tenancy
6. Assessment Tenancy
7. Teacher Payout Tenancy
8. Notification Domain
9. Video Provider Abstraction
10. Bulk Import
11. Audit Trail Completion, Hardening & Workflow Integration

## Current repository warning

The root `CLAUDE.md` in `main` is stale and still describes Phase 10 / commit `52107315...`.

Replace it with this file's guidance.

Phase 11 is complete. Do not redo it unless Phase 12 discovers a real regression.

The active audit app is:

```text
audit_logs/
```

Do not recreate the removed empty `audit/` app.

# Phase 12

**SaaS Phase 12 — API Contract Stabilization & Frontend Readiness**

The purpose is to stabilize the mature backend API so a frontend can consume it from an explicit OpenAPI-backed contract.

This is not a frontend implementation phase.

## Core rules

1. Backend remains the source of truth.
2. Preserve tenant isolation.
3. Preserve existing authorization architecture.
4. Do not invent undocumented behavior.
5. OpenAPI must match implementation.
6. Do not make silent breaking API changes.
7. Keep errors machine-readable and safe.
8. Keep dates timezone-aware and explicit.
9. Keep enums stable.
10. Preserve PostgreSQL compatibility.
11. Preserve existing transaction/concurrency protections.
12. Do not build the frontend in this phase.
13. Do not add unrelated product domains.
14. Full regression and OpenAPI gates are mandatory.

# Required work

## 1. Inventory

Inventory every public endpoint and record:

```text
method
path
auth
tenant scope
roles
request
response
status codes
errors
pagination
filters
side effects
audit behavior
```

Use actual code/tests.

## 2. OpenAPI

Audit the generated schema against implementation.

Fix:

- missing endpoints
- wrong request/response schemas
- required/optional errors
- nullable errors
- enums
- status codes
- parameters
- pagination
- authentication documentation
- stale descriptions

## 3. Authentication

Document the actual authentication system:

- login
- logout if present
- password change
- password reset
- sessions/tokens
- expiry/refresh if present
- CSRF where applicable
- 401 behavior

Never invent refresh-token behavior.

## 4. Tenancy

Maintain:

```text
authenticated user
→ organization membership
→ organization route
→ role permission
→ tenant queryset
```

Academy A must never access Academy B.

Known cross-tenant IDs must not bypass tenant protection.

## 5. Roles

Use actual repository roles.

Where applicable:

```text
OWNER
ADMIN
STAFF
TEACHER
STUDENT
PARENT
```

Document allowed/denied roles for sensitive endpoints.

Do not create frontend-only permissions.

## 6. Serializers/responses

Audit:

- field names
- required fields
- nullable fields
- nested structures
- enums
- IDs
- timestamps
- pagination

Avoid unnecessary breaking changes.

## 7. Errors

Document real behavior for:

```text
400
401
403
404
409 where applicable
429 where applicable
```

Never expose secrets, SQL errors or stack traces.

## 8. Pagination/filtering

Document actual:

```text
page
page_size
ordering
count
next
previous
filters
```

Tenant scope must be applied before filtering.

## 9. Date/time

Use timezone-aware ISO 8601 values.

Explicitly document timezone semantics for:

```text
availability
bookings
cohorts
assessments
payouts
notifications
audit
imports
```

## 10. Enums

Audit public enums.

Machine values must remain stable.

## 11. Uploads

Document private storage and signed URLs.

Never add public media routes.

Bulk import remains:

```text
upload → validate → commit → result
```

Do not expose spreadsheet contents.

## 12. Audit API

The active audit domain is `audit_logs/`.

Keep audit routes organization-scoped:

```text
GET /api/organizations/<organization_pk>/audit-logs/
GET /api/organizations/<organization_pk>/audit-logs/<id>/
```

Do not add audit PATCH/PUT/DELETE.

Document supported filters and `X-Request-ID`.

## 13. Bulk import

Verify and document:

- formats
- columns
- validation
- transactional behavior
- errors
- counts
- audit events

Preserve the current all-or-nothing behavior.

## 14. HTTP semantics

Review action endpoints:

```text
finalize
cancel
withdraw
reset
validate
commit
```

Document actual semantics/status codes. Do not redesign blindly.

## 15. Retry behavior

Review:

```text
booking creation
membership changes
bulk import
payout finalization
notification sending
video meeting creation
```

Determine whether retry is safe or duplicates/conflicts occur.

Do not build a universal idempotency framework without evidence.

## 16. Concurrency

Do not replace backend locking/transactions with frontend checks.

Add API-level coverage for high-risk duplicate operations where useful.

## 17. CORS

Verify production/development CORS, credentials, auth headers and CSRF behavior.

Never solve frontend problems with permissive production CORS.

## 18. Contract tests

At minimum test:

- authentication
- tenancy
- roles
- serializers
- errors
- pagination
- filters
- audit
- bulk import
- cross-tenant access

# Documentation

Update as needed:

```text
CLAUDE.md
README.md
specs/saas/
learnings.md
tech-debt.md
```

Record:

```text
baseline
API decisions
breaking changes
known gaps
deferrals
next phase
```

# Verification

Run:

```bash
python manage.py check
python manage.py check --deploy --fail-level WARNING
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Also run the existing OpenAPI/schema validation mechanism.

Use PostgreSQL, not SQLite.

# Definition of done

Phase 12 passes only when:

- all public APIs are inventoried;
- OpenAPI matches implementation;
- authentication is explicit;
- tenant and role behavior is explicit;
- serializers are stable;
- errors are predictable;
- pagination/filtering are tested;
- timestamps are unambiguous;
- enums are stable;
- uploads are documented;
- bulk import is documented;
- audit API is documented;
- request correlation is documented;
- high-risk retry behavior is understood;
- tenant isolation remains intact;
- PostgreSQL gates pass;
- OpenAPI validation passes;
- full regression passes;
- root CLAUDE.md is current;
- learnings/tech debt are updated;
- next phase is named.

# Explicit non-goals

Do not implement:

- complete frontend
- mobile app
- payment gateway
- accounting
- analytics warehouse
- event sourcing
- message queue
- microservices
- separate database per academy
- AI features
- unnecessary API versioning
- second authorization system

# Next phase

Expected:

**SaaS Phase 13 — Frontend Foundation / Web Application**

Finalize Phase 13 only after Phase 12 identifies and closes API gaps that block frontend work.
# Phase 12 API Contract Decisions & Current Architecture
- **Repository**: `adewoye-saheed-dML/iqralms` (target branch `main`).
- **Authority vs Participation**:
  - `OrganizationMembership` = authority and access to the academy.
  - `StudentEnrollment` = academic participation in the academy (`StudentEnrollment.objects.active()`, `active_enrollment()`, `is_active_student_participant()`).
- **Academy Configuration Authoritative**:
  - `OrganizationTeacherConfiguration` is authoritative for academy-scoped teacher approval (`approved`), weekly capacity (`max_weekly_hours`), and hourly payout rates (`hourly_payout_rate`).
  - No fallback to global `TeacherProfile` for academy-scoped scheduling or payouts.
- **Invitation Acceptance**:
  - Atomic transaction: validates invitation, creates active membership, then marks invitation accepted.
  - Rejects existing members (active or suspended) without modifying membership role or status. Owner role cannot be invited.
- **Organization Listing**:
  - `/api/organizations/mine/` filters `organization__is_active=True`.
- **OpenAPI Contract**:
  - Concrete request/response serializers for student enrollment collection and detail endpoints.
- **Next Phase**: SaaS Phase 13 — Frontend Foundation / Web Application.
