# SaaS Phase 12 — API Contract Stabilization & Frontend Readiness

## Status
READY FOR IMPLEMENTATION

## Repository baseline

Repository: `adewoye-saheed-dML/quran_acad`
Target branch: `main`

Verified `main` head on 2026-09-12:

`81c79dbceb50858c64a2c94eb36c52e3ed957e9a`

Latest commit:

`feat(Audit): completed`

Phase 11 implementation was completed immediately before this commit. The repository is now past SaaS Phase 11.

Completed phases:
- Phase 1 — Organization Foundation
- Phase 2 — Accounts Tenancy
- Phase 3 — Curriculum Tenancy
- Phase 4 — Scheduling Tenancy
- Phase 5 — Pricing Tenancy
- Phase 6 — Assessment Tenancy
- Phase 7 — Teacher Payout Tenancy
- Phase 8 — Notification Domain
- Phase 9 — Video Provider Abstraction
- Phase 10 — Bulk Import
- Phase 11 — Audit Trail Completion, Hardening & Workflow Integration

## Important current-repository finding

The root `CLAUDE.md` currently committed to `main` is stale: it still identifies Phase 10 and commit `52107315...` as the current baseline.

Replace it with the Phase 12 Claude instructions supplied with this package.

The repository's active audit implementation is `audit_logs/`. The old empty `audit/` scaffold was removed during Phase 11. Do not recreate it.

---

# 1. Phase objective

Freeze and stabilize the backend API contract sufficiently for a real frontend/client to be built against it without repeatedly reverse-engineering backend behavior.

Target flow:

```text
Django domain models/services
        ↓
stable serializers/views
        ↓
predictable permissions/errors
        ↓
OpenAPI contract
        ↓
frontend/client
```

This is NOT a frontend implementation phase.

The backend remains the source of truth.

---

# 2. Why Phase 12 exists

The SaaS backend now contains the major product domains:

- organizations and memberships
- accounts
- curriculum
- scheduling
- pricing
- assessment
- payouts
- notifications
- video provider abstraction
- bulk import
- audit logging

The next risk is no longer simply missing backend domains. It is an inconsistent or poorly documented API that forces frontend developers to infer behavior from Django code.

Phase 12 turns the existing API into a deliberate product contract.

---

# 3. Non-negotiable rules

1. Do not redesign working domain models merely for cosmetic API consistency.
2. Do not weaken tenant isolation.
3. Do not introduce a second authorization system.
4. Do not expose database internals unnecessarily.
5. Do not document behavior that the implementation does not provide.
6. Every public endpoint must have accurate OpenAPI documentation.
7. OpenAPI must agree with serializers, views and actual HTTP status codes.
8. Authentication behavior must be explicit.
9. Organization-scoped behavior must be explicit.
10. Role restrictions must be explicit.
11. Error responses must be predictable enough for clients to handle.
12. Pagination and filtering must be documented.
13. Date/time semantics must be unambiguous.
14. Do not silently introduce breaking API changes.
15. Do not rely on the frontend for security decisions.
16. Preserve PostgreSQL compatibility.
17. Preserve existing concurrency protections.
18. Do not build a complete frontend in Phase 12.
19. Do not add payment, accounting, analytics warehouse, AI or event-sourcing infrastructure.
20. Do not declare completion until OpenAPI, PostgreSQL and regression gates pass.

---

# 4. Workstream 12.1 — Complete API inventory

Inventory every public API endpoint from the actual repository.

For each endpoint record:

```text
method
path
purpose
authentication requirement
organization/tenant requirement
allowed roles
path parameters
query parameters
request body
success status
success response
error statuses
pagination
filters
side effects
audit event where applicable
```

Use the live code and tests as the source of truth.

Do not invent endpoints solely to make the inventory look complete.

---

# 5. Workstream 12.2 — OpenAPI contract audit

Compare the generated OpenAPI schema with implementation.

Correct:

- missing endpoints
- incorrect request serializers
- incorrect response serializers
- required/optional field mistakes
- nullable field mistakes
- missing enums
- wrong status codes
- missing error responses
- missing query parameters
- wrong path parameter types
- incorrect pagination documentation
- stale descriptions

The schema must describe actual behavior.

Do not add annotations merely to satisfy a checklist.

---

# 6. Workstream 12.3 — Authentication contract

Inspect the real authentication architecture.

Document:

- login
- logout if implemented
- password change
- password reset
- token/session behavior
- authentication requirements
- expiry/refresh behavior if implemented
- CSRF behavior where applicable
- 401 behavior

Do not invent refresh-token behavior.

Do not weaken authentication to make frontend integration easier.

---

# 7. Workstream 12.4 — Tenant contract

The authority chain remains:

```text
authenticated user
        ↓
organization membership
        ↓
organization-scoped route
        ↓
role/permission check
        ↓
tenant-scoped queryset
```

Verify:

- Academy A cannot access Academy B.
- Known cross-tenant IDs cannot bypass tenancy.
- Suspended members remain restricted.
- Non-members remain restricted.
- Client-supplied organization IDs never override authorization.

Never rely on frontend tenant selection for security.

---

# 8. Workstream 12.5 — Role contract

Use only roles that actually exist in the repository.

Where applicable:

```text
OWNER
ADMIN
STAFF
TEACHER
STUDENT
PARENT
```

For sensitive endpoints document:

```text
allowed roles
denied roles
```

Use existing permission helpers/classes.

Do not create frontend-only permissions.

---

# 9. Workstream 12.6 — Response contract

Audit serializers for:

- field names
- required fields
- nullable fields
- nested structures
- enum values
- timestamps
- identifiers
- pagination

Do not introduce a global response envelope unless there is a demonstrated product need.

Avoid unnecessary breaking changes.

---

# 10. Workstream 12.7 — Error contract

Make error behavior predictable.

Where supported by the real implementation, document:

```text
400 validation error
401 unauthenticated
403 forbidden
404 not found
409 conflict
429 throttled
```

Only advertise statuses actually returned.

Validation errors should remain machine-readable.

Never expose:

```text
stack traces
SQL errors
credentials
tokens
private storage paths
internal secrets
```

---

# 11. Workstream 12.8 — Pagination

Audit all collection endpoints.

Document:

```text
default page size
maximum page size
page parameter
ordering
next/previous
count
```

Do not add pagination to small bounded resources without a reason.

For audit logs preserve deterministic ordering, preferably:

```text
-created_at
-id
```

if consistent with the implementation.

---

# 12. Workstream 12.9 — Filtering and sorting

Inventory actual filters.

For every supported filter document:

```text
name
type
allowed values
date format
example
```

Tenant scoping must be applied before user filters.

Do not expose arbitrary model-field filtering.

---

# 13. Workstream 12.10 — Date/time contract

Use timezone-aware ISO 8601 timestamps.

Document the canonical timezone representation.

Pay special attention to:

- availability
- bookings
- cohorts
- assessments
- payouts
- notifications
- audit logs
- bulk imports

Frontend developers must not have to guess whether a timestamp is UTC or local.

---

# 14. Workstream 12.11 — Enum stability

Audit public enums, including where applicable:

```text
organization roles
membership status
booking status
assessment status
pricing status
payout status
notification status
video provider
audit action
bulk import status
```

Machine values must be stable.

Display labels must not become API identifiers.

---

# 15. Workstream 12.12 — ID contract

Document the public identifier type/format for major resources.

Possible existing forms include:

```text
integer
UUID
string
```

Do not change identifiers merely for stylistic reasons.

Audit generic targets remain safely represented by:

```text
object_type
object_id
```

---

# 16. Workstream 12.13 — File upload/download contract

The repository uses private storage.

Document:

- allowed upload formats
- multipart requirements
- validation
- size restrictions where implemented
- private storage behavior
- signed URL behavior
- expiry behavior

Never add a public media route.

For bulk imports, document:

```text
upload → validate → commit → result
```

Never expose raw spreadsheet contents unnecessarily.

---

# 17. Workstream 12.14 — Bulk import contract

Stabilize the Phase 10/11 import API.

Document:

- supported formats
- required columns
- optional columns
- validation behavior
- transaction behavior
- row-level errors
- created/skipped/failed counts
- audit events

Preserve the existing all-or-nothing transaction behavior where that is the current contract.

---

# 18. Workstream 12.15 — Audit API contract

The active audit domain is:

```text
audit_logs/
```

Document:

```text
GET /api/organizations/<organization_pk>/audit-logs/
GET /api/organizations/<organization_pk>/audit-logs/<id>/
```

Where implemented, document:

```text
action
actor
object_type
object_id
created_after
created_before
page
page_size
```

Audit records remain immutable.

Do not add PATCH/PUT/DELETE audit endpoints.

---

# 19. Workstream 12.16 — Request correlation

Phase 11 introduced request correlation.

Verify and document:

```text
X-Request-ID
```

Define:

- whether clients may supply it
- maximum length
- server generation when absent
- response behavior
- relationship to audit records

Request IDs are tracing identifiers, never authentication credentials.

---

# 20. Workstream 12.17 — HTTP semantics

Review action-style endpoints such as:

```text
finalize
cancel
withdraw
reset
validate
commit
```

Document actual semantics and status codes.

Do not blindly redesign existing methods.

---

# 21. Workstream 12.18 — Retry and duplicate behavior

Review high-risk operations:

```text
booking creation
membership changes
bulk import
payout finalization
notification sending
video meeting creation
```

For each determine:

```text
retry safe?
duplicate prevented?
conflict returned?
idempotency supported?
```

Do not build a universal idempotency framework unless actual domain requirements justify it.

At minimum, document retry behavior.

---

# 22. Workstream 12.19 — Concurrency-sensitive endpoints

Verify that frontend retries cannot bypass existing scheduling transaction/locking protections.

Do not replace backend locking with frontend checks.

Add API-level coverage for the highest-risk duplicate operation if missing.

---

# 23. Workstream 12.20 — CORS/frontend readiness

Inspect deployment configuration.

Verify:

- allowed origins
- credentials
- authorization headers
- CSRF behavior
- development vs production differences

Never ship:

```python
CORS_ALLOW_ALL_ORIGINS = True
```

as a production shortcut.

---

# 24. Workstream 12.21 — Contract tests

Add/strengthen tests for:

### Authentication
- unauthenticated requests
- authenticated requests
- invalid credentials
- password-related validation

### Tenancy
- same organization
- cross organization
- suspended member
- non-member
- role restriction

### Serialization
- required fields
- optional fields
- nullability
- enums
- timestamps
- nested relationships

### Errors
- invalid input
- invalid ID
- forbidden
- not found
- malformed filters

### Pagination
- first page
- later page
- custom size
- maximum size
- stable ordering

### Audit
- list
- detail
- filters
- request ID
- metadata redaction

### Bulk import
- valid upload
- invalid upload
- validation
- commit result
- audit result

---

# 25. Workstream 12.22 — Backward compatibility

Before changing an endpoint inspect:

```text
tests
OpenAPI
README
CLAUDE.md
specs
frontend references if any
```

If a breaking change is necessary:

1. document it;
2. update tests;
3. update OpenAPI;
4. update documentation;
5. document migration impact.

Never make a breaking change silently.

---

# 26. API versioning decision

Explicitly decide whether to keep:

```text
/api/...
```

or introduce:

```text
/api/v1/...
```

Do not introduce versioning merely because it is fashionable.

If the product is still pre-production and no external clients exist, retaining the current unversioned API can be the correct decision.

Document the decision.

---

# 27. Documentation deliverables

Update where appropriate:

```text
CLAUDE.md
README.md
specs/saas/
learnings.md
tech-debt.md
```

Record:

```text
current baseline commit
API contract decisions
breaking changes
known limitations
deliberate deferrals
next phase
```

---

# 28. OpenAPI quality gate

Run the repository's actual OpenAPI generation/validation mechanism.

The resulting schema must:

- load successfully;
- contain no broken references;
- contain no placeholder endpoint descriptions;
- accurately represent enums;
- accurately represent required fields;
- accurately represent nullable fields;
- accurately represent response codes;
- accurately represent pagination;
- accurately represent authentication;
- include audit endpoints;
- include bulk-import endpoints.

Do not add a heavy schema dependency without justification.

---

# 29. PostgreSQL gate

Run against PostgreSQL:

```bash
python manage.py check
python manage.py check --deploy --fail-level WARNING
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Run any existing OpenAPI/schema validation command as well.

Do not substitute SQLite when the repository requires PostgreSQL.

---

# 30. Definition of done

Phase 12 is complete only when:

1. Public API endpoints are inventoried.
2. OpenAPI accurately describes implementation.
3. Authentication behavior is documented.
4. Tenant behavior is documented.
5. Role behavior is documented.
6. Serializer contracts are stable.
7. Error behavior is predictable and documented.
8. Pagination/filtering are documented and tested.
9. Date/time semantics are explicit.
10. Public enums are stable.
11. Upload/private-storage behavior is documented.
12. Bulk import behavior is documented.
13. Audit API behavior is documented.
14. Request correlation is documented.
15. Retry/duplicate behavior of high-risk operations is understood.
16. No tenant-isolation regression exists.
17. PostgreSQL checks pass.
18. OpenAPI/schema validation passes.
19. Full regression tests pass.
20. Root `CLAUDE.md` is current.
21. Learnings/tech debt are updated where appropriate.
22. The next phase is explicitly identified.

---

# 31. Explicit non-goals

Do not implement:

- complete React/Vue/Next frontend
- mobile application
- payment gateway
- accounting
- analytics warehouse
- event sourcing
- message queue
- microservices
- separate database per academy
- AI features
- unnecessary API versioning
- second authorization framework

---

# 32. Expected outcome

A frontend engineer should be able to use the generated OpenAPI contract and repository documentation to understand:

```text
how to authenticate
how to select an academy
which roles can act
what to send
what comes back
what errors mean
how pagination works
how dates work
how uploads work
how bulk import works
how audit history works
```

without reverse-engineering Django internals.

---

# 33. Next phase

Expected direction after Phase 12:

**SaaS Phase 13 — Frontend Foundation / Web Application**

However, Phase 13 must be finalized after Phase 12 identifies any API gaps that genuinely block frontend development.

Do not begin frontend implementation until the Phase 12 contract gate passes.
