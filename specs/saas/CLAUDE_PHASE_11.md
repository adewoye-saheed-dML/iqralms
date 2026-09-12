# Quran Academy SaaS Development Rules

## Repository baseline

Repository: `adewoye-saheed-dML/quran_acad`  
Target branch: `main`

### Current repository state inspected for Phase 11

Current `main` head:

```text
763ec118c3d42a0b12e116ea1d5f3ea04567c712
```

Current head commit:

```text
feat(audit):phase 11
```

Completed SaaS phases:

- SaaS Phase 1 — Organization Foundation
- SaaS Phase 2 — Accounts Tenancy
- SaaS Phase 3 — Curriculum Tenancy
- SaaS Phase 4 — Scheduling Tenancy
- SaaS Phase 5 — Pricing Tenancy
- SaaS Phase 6 — Assessment Tenancy
- SaaS Phase 7 — Teacher Payout Tenancy
- SaaS Phase 8 — Notification Domain
- SaaS Phase 9 — Video Provider Abstraction
- SaaS Phase 10 — Bulk Import

The current repository already contains a first Phase 11 audit implementation. Therefore the active task is **SaaS Phase 11 — Audit Trail Completion, Hardening & Workflow Integration**.

Do not treat Phase 11 as a greenfield build.

---

# Phase 11 objective

Deliver a usable, tenant-safe, append-only audit trail that is automatically populated by important SaaS operations.

The goal is:

```text
authorized business mutation
        ↓
central audit service
        ↓
immutable audit record
        ↓
organization-scoped read API
```

The phase is not complete merely because an `AuditLog` model and GET endpoints exist.

---

# Current implementation facts

The current repository has an active `audit_logs` app containing:

- `AuditLog` model
- append-only `save()` / `delete()` guards
- `record_event()` service
- recursive sensitive-key sanitization
- actor active-membership checks
- cross-organization target checks
- organization-scoped list/detail API
- pagination
- basic filters
- OpenAPI annotations
- tests
- existing membership audit integration

The current root settings register:

```python
"audit_logs",
```

The root URL configuration includes audit routes under:

```text
/api/organizations/
```

The separate repository directory:

```text
audit/
```

also exists as a second audit-related Django scaffold and must be deliberately resolved.

---

# Non-negotiable audit rules

1. Every tenant-owned audit event belongs to exactly one organization.
2. User-generated events must have a valid active membership in that organization.
3. System-generated events must use an explicit system-actor convention.
4. Audit records are append-only.
5. Normal application code must not update or delete an existing audit record.
6. Audit records must not disappear through accidental organization cascades.
7. No global unscoped audit read endpoint may exist.
8. A user in Academy A must never read Academy B's audit records.
9. Known audit IDs from another academy must not bypass tenancy.
10. Filters must never widen tenant scope.
11. Suspended and non-members must not use restricted audit endpoints.
12. Do not invent a second authorization system.
13. Do not scatter `AuditLog.objects.create(...)` across unrelated views.
14. All audit writes must go through the central audit service.
15. Sensitive values must never be persisted.
16. Raw uploaded files and spreadsheet contents must never be logged.
17. Do not log every GET request or ordinary read.
18. Record only events that materially improve accountability.
19. Do not mark Phase 11 complete until integration, adversarial tests and the PostgreSQL gate pass.
20. Always document deliberate deferrals and the next phase before closure.

---

# Canonical audit application

Prefer:

```text
audit_logs/
```

as the sole audit domain.

Before deleting `audit/`, verify:

```text
INSTALLED_APPS
imports
URL configuration
migrations
tests
documentation
management commands
```

If the scaffold is unused, remove it cleanly.

Do not maintain two competing audit implementations.

---

# Audit action taxonomy

Do not allow arbitrary action strings.

Use a central constant/choice namespace.

Preferred naming:

```text
auth.login_success
auth.login_failed
auth.logout
auth.password_changed
auth.password_reset_requested

organization.created
organization.settings_updated

membership.created
membership.role_changed
membership.suspended
membership.reactivated

permission.changed

curriculum.track_created
curriculum.track_updated
curriculum.track_archived
curriculum.level_created
curriculum.level_updated
curriculum.level_archived
curriculum.teacher_assignment_changed

enrollment.created
enrollment.updated
enrollment.withdrawn

availability.created
availability.updated
availability.deleted

booking.created
booking.updated
booking.cancelled
booking.completed
booking.no_show

cohort.created
cohort.updated
cohort.cancelled
cohort.assignment_changed

assessment.created
assessment.updated
assessment.finalized
assessment.reopened

pricing.agreement_created
pricing.agreement_updated
pricing.agreement_deactivated
pricing.rate_changed

payout.created
payout.finalized
payout.reopened

notification.provider_updated
notification.configuration_updated
notification.sent

video.provider_updated
video.meeting_created
video.meeting_updated
video.meeting_cancelled

bulk_import.started
bulk_import.validated
bulk_import.completed
bulk_import.failed
```

Only use actions that correspond to real workflows in the current repository.

Do not invent invitation events until an actual invitation lifecycle exists.

Do not create dozens of low-value events.

---

# Audit model requirements

Use a small model.

Conceptually:

```text
id
organization
actor
actor_type
actor_id_snapshot
action
object_type
object_id
metadata
request_id
created_at
```

`actor_id_snapshot` is required only if needed to preserve actor identity after a user is deleted.

## Organization deletion

Do not use an organization `CASCADE` that silently deletes audit history.

Preferred:

```python
on_delete=models.PROTECT
```

or another explicitly documented immutable-history strategy.

## Actor

Recommended convention:

```text
actor_type = USER
actor = user
```

for human actions.

```text
actor_type = SYSTEM
actor = null
```

for system actions.

Do not let `actor = NULL` remain semantically ambiguous.

---

# Audit service

Use one canonical function:

```python
record_event(
    *,
    organization,
    actor=None,
    action,
    target=None,
    metadata=None,
    request_id=None,
)
```

The service must:

1. validate actor;
2. validate organization context;
3. validate action;
4. reject cross-tenant targets;
5. normalize object type/id;
6. sanitize metadata;
7. resolve request ID when appropriate;
8. persist the event.

Domain code must call the service, not the model manager directly.

---

# Transaction rules

Prefer:

```python
with transaction.atomic():
    mutate_business_state()
    record_event(...)
```

The audit event must describe the resulting business action accurately.

Do not log a success event before the underlying transaction can still fail.

For security-critical events, prefer fail-closed behavior when the repository's requirements demand accountability.

For non-security-critical operational events, best-effort behavior is permitted only when explicitly documented and tested.

Never silently swallow audit exceptions.

---

# Request correlation

The model already has:

```text
request_id
```

but Phase 11 requires actual propagation.

Use:

```text
X-Request-ID
```

unless the repository already has a canonical correlation mechanism.

Implement lightweight middleware/helper behavior that:

- accepts a bounded request ID;
- generates one when absent;
- stores it on request state;
- makes it available to the audit service;
- can return it in the response if that is the chosen API convention.

Do not use request IDs as authorization.

Background/system tasks may supply explicit request/correlation IDs.

---

# Metadata privacy rules

Audit metadata is structured JSON, not a dump of application state.

Never persist:

```text
password
password_hash
new_password
old_password

token
access_token
refresh_token

api_key
secret
secret_key
client_secret

private_key
credential
credentials

authorization
authorization_header

cookie
session
session_key

raw uploaded file bytes
raw CSV/XLSX contents
full request headers
full stack traces
provider secrets
```

Use recursive sanitization.

Known secret-bearing keys should normally be replaced with:

```text
[REDACTED]
```

Potentially sensitive opaque payloads should be rejected or replaced rather than blindly serialized.

Do not attempt unreliable secret detection across every arbitrary string.

Prefer explicit safe metadata.

---

# Before/after summaries

When an important change occurs, prefer:

```json
{
  "changes": {
    "role": {
      "from": "teacher",
      "to": "admin"
    }
  }
}
```

over full object serialization.

Use a reusable helper for selected-field diffs.

Do not include unchanged fields.

Do not include entire domain objects.

---

# Tenant isolation

The tenant path must remain:

```text
authenticated user
      ↓
organization URL
      ↓
active membership for that exact organization
      ↓
organization-scoped queryset
      ↓
object lookup
```

Never trust an organization ID supplied in a request body as the source of tenant authority.

A client-supplied organization reference must never let an Academy A user write an audit record for Academy B.

---

# Read API

Use:

```text
GET /api/organizations/<organization_pk>/audit-logs/
GET /api/organizations/<organization_pk>/audit-logs/<id>/
```

No global audit endpoint.

Preferred read roles:

```text
Owner -> allow
Admin -> allow
Staff -> deny unless explicitly justified
Teacher -> deny
Student -> deny
Parent -> deny
Suspended -> deny
Non-member -> deny
Unauthenticated -> deny
```

Use the repository's existing `CanManageOrganizationMemberships` and tenant helpers rather than inventing a separate audit authorization framework.

Known IDs from other tenants should return the repository's safe non-disclosure behavior, normally 404 where object existence must not be revealed.

---

# Filtering

Supported filters may include:

```text
action
actor
object_type
object_id
created_after
created_before
```

All filters must execute on a queryset already scoped to the authenticated organization.

Do not allow filters to widen scope.

Parse dates as timezone-aware ISO 8601 values.

Bound actor/object string sizes.

Reject malformed inputs cleanly.

---

# Pagination

Keep deterministic pagination.

Current intended baseline:

```text
page_size = 50
max_page_size = 1000
```

Test:

```text
page 1
page 2
custom page size
maximum page size
stable ordering
tenant isolation across pages
```

Preferred ordering:

```text
-created_at
-id
```

---

# Immutability

Instance-level guards alone are insufficient because Django queryset operations can bypass `save()` and `delete()`.

Explicitly protect against:

```python
AuditLog.objects.filter(...).update(...)
AuditLog.objects.filter(...).delete(...)
```

Normal application paths must provide:

```text
no PATCH
no PUT
no DELETE
no write serializer
```

Django admin must be read-only or must not expose the model.

Because PostgreSQL is canonical, implement a database-level guard if practical:

```text
INSERT -> allowed
UPDATE -> rejected
DELETE -> rejected
```

Prefer a PostgreSQL trigger/function in a migration.

Do not claim this prevents a privileged PostgreSQL superuser from modifying data; the requirement is application-level and database-level normal immutability.

---

# Indexing

Optimize for actual tenant/date/action queries.

Reasonable candidates:

```text
(organization, -created_at)
(organization, action, -created_at)
(organization, actor, -created_at)
(organization, object_type, object_id, -created_at)
```

Keep the final index set minimal and evidence-based.

---

# Workflow integration

Audit at authoritative business mutation boundaries.

Do not use generic `save()` signals for the entire platform merely because they are convenient.

Do not audit every model mutation automatically.

## Organizations

Audit meaningful:

```text
organization.created
organization.settings_updated
```

## Membership

Verify and harden the existing integration:

```text
membership.created
membership.role_changed
membership.suspended
membership.reactivated
```

## Authentication

Where the current auth architecture makes it reliable:

```text
auth.login_success
auth.login_failed
auth.logout
auth.password_changed
auth.password_reset_requested
```

Never record passwords/tokens.

## Curriculum

Audit actual administrative mutations, e.g.:

```text
track/level creation
track/level updates
archive operations
teacher-track assignment changes
```

## Scheduling

Audit meaningful mutations:

```text
availability
booking
cohort
teacher assignment/routing changes
```

Do not audit ordinary reads.

## Assessment

Audit meaningful state changes actually present in the codebase.

## Pricing

Audit agreement/rate changes actually present in the codebase.

## Payouts

Audit meaningful financial state transitions actually present in the codebase.

Never log provider secrets or bank credentials.

## Notifications

Audit provider/configuration changes and meaningful message-sent events when useful.

Do not make the audit log a duplicate delivery-attempt database.

## Video

Audit provider/configuration and meaningful meeting lifecycle events.

## Bulk imports

The Phase 10 import lifecycle must be auditable.

Preferred events:

```text
bulk_import.started
bulk_import.validated
bulk_import.completed
bulk_import.failed
```

Safe metadata:

```text
import_job_id
kind
row_count
valid_row_count
invalid_row_count
created_count
reused_count
error_count
```

Never store raw spreadsheet contents.

---

# Test requirements

Phase 11 tests are mandatory.

## Model/service

Test:

- valid event creation;
- required organization;
- user/system actor semantics;
- known action;
- unknown action rejection;
- timezone-aware timestamps;
- target normalization;
- cross-tenant target rejection;
- recursive redaction;
- request ID.

## Tenant isolation

Test:

```text
A list cannot read B
A detail cannot read B
A known B ID cannot be read
A cannot filter into B
A cannot record B targets
```

## Permission matrix

Test:

```text
owner -> allow
admin -> allow
staff -> deny unless policy changes
teacher -> deny
student -> deny
parent -> deny
suspended -> deny
non-member -> deny
anonymous -> deny
```

## Immutability

Attempt:

```text
instance save
instance delete
QuerySet.update
QuerySet.delete
serializer update
PATCH
PUT
DELETE
```

All must fail or be unavailable.

## Pagination

Test:

```text
default
custom
max
page 1
page 2
ordering
tenant isolation
```

## Filters

Test each filter and combinations while preserving tenant scope.

## Redaction

At minimum test:

```text
password
password_hash
new_password
old_password
token
access_token
refresh_token
api_key
secret
secret_key
client_secret
private_key
credential
authorization
authorization_header
cookie
session_key
```

including nested dict/list structures and mixed case.

## Integration

Verify events are actually emitted for the selected high-value workflows.

Do not declare integration complete from service unit tests alone.

---

# Admin

Preferred:

- audit logs visible read-only;
- no add;
- no change;
- no delete;
- useful list filters/search if practical.

Alternative:

- do not register the model.

Do not leave an apparently editable audit admin.

---

# OpenAPI

Document:

```text
GET list
GET detail
pagination
filters
authorization
404/403 behavior
```

Do not expose audit write endpoints.

---

# PostgreSQL phase gate

The canonical database is PostgreSQL.

Required:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Phase 11 is not complete based solely on SQLite.

If a PostgreSQL trigger/function is added, inspect the generated migration/SQL and test it against PostgreSQL.

---

# Documentation closure

Before Phase 11 is closed, update:

```text
CLAUDE.md
specs/saas/SaaS Phase 11 — Audit Log.md
learnings.md
tech-debt.md
OpenAPI/schema outputs where tracked
```

Document:

- action taxonomy;
- actor/system convention;
- request ID behavior;
- redaction policy;
- organization deletion policy;
- immutability mechanism;
- admin decision;
- event failure semantics;
- deliberate deferrals;
- next phase.

---

# Scope discipline

Do not implement in Phase 11:

```text
event sourcing
Kafka/RabbitMQ
distributed event bus
analytics warehouse
AI anomaly detection
full compliance certification
frontend audit dashboard
payment gateway
accounting/tax features
separate database per academy
```

Do not:

```text
log every GET
store full serialized models
store raw uploads
create tenant IDs in arbitrary request bodies as authority
create a second permission system
```

---

# Definition of done

Phase 11 is complete only when:

## Audit domain

- [ ] `audit_logs` is the sole canonical audit app.
- [ ] duplicate `audit/` scaffold has been deliberately resolved.
- [ ] action vocabulary is controlled.
- [ ] organization ownership is mandatory.
- [ ] actor/system semantics are explicit.
- [ ] target identity is captured.
- [ ] timestamps are timezone-aware.
- [ ] appropriate indexes exist.

## Security

- [ ] actor is valid for the organization;
- [ ] suspended members are blocked;
- [ ] unauthorized roles are blocked;
- [ ] cross-tenant target writes are rejected;
- [ ] cross-tenant reads are rejected;
- [ ] known IDs do not bypass tenancy;
- [ ] filters do not widen scope;
- [ ] sensitive metadata is redacted;
- [ ] raw uploaded files are never logged.

## Immutability

- [ ] instance updates blocked;
- [ ] instance deletes blocked;
- [ ] queryset updates blocked;
- [ ] queryset deletes blocked;
- [ ] no write serializer;
- [ ] no write endpoints;
- [ ] admin is read-only or absent;
- [ ] database-level protection is implemented where practical;
- [ ] organization deletion cannot silently erase audit history.

## Correlation

- [ ] request IDs are propagated/generated;
- [ ] audit events can capture them;
- [ ] background/system events can supply explicit IDs.

## Integration

- [ ] organization mutations audited;
- [ ] membership mutations audited;
- [ ] high-value curriculum mutations audited;
- [ ] high-value scheduling mutations audited;
- [ ] high-value assessment mutations audited;
- [ ] high-value pricing mutations audited;
- [ ] payout actions audited;
- [ ] relevant notification/video configuration/actions audited;
- [ ] bulk import lifecycle audited;
- [ ] supported auth events audited.

## API

- [ ] tenant-scoped list/detail endpoints;
- [ ] safe filters;
- [ ] safe pagination;
- [ ] deterministic ordering;
- [ ] accurate OpenAPI;
- [ ] no audit write endpoint.

## Tests

- [ ] model/service;
- [ ] tenant isolation;
- [ ] role matrix;
- [ ] immutability;
- [ ] redaction;
- [ ] request ID;
- [ ] filters;
- [ ] pagination;
- [ ] integration;
- [ ] bulk import;
- [ ] full regression.

## Final gate

All must pass:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

against the canonical PostgreSQL environment.

---

# Next phase rule

Do not invent a new Phase 12 objective during implementation.

At Phase 11 closure, document the next phase explicitly in:

```text
CLAUDE.md
learnings.md
tech-debt.md
```

The next phase should focus on the next approved SaaS roadmap boundary, not unrelated cleanup discovered opportunistically during audit work.
