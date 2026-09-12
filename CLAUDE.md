# Quran Academy SaaS Development Rules

## Repository baseline

Repository: `adewoye-saheed-dML/quran_acad`
Target branch: `main`

Current completed SaaS phase: **SaaS Phase 10 — Bulk Import**

Current verified `main` head:
`52107315cde805be465334970961de5c4116abd7`

Commit message:
`feat(saas): complete Phase 10 bulk import and update OpenAPI schema`

Completed SaaS phases:
- SaaS Phase 1 — Organization Foundation
- SaaS Phase 2 — Accounts Tenancy
- SaaS Phase 3 — Curriculum Tenancy
- SaaS Phase 4 — Scheduling Tenancy
- SaaS Phase 5 — Pricing Tenancy
- SaaS Phase 6 — Assessment Tenancy
- SaaS Phase 7 — Teacher Payout Tenancy
- SaaS Phase 8 — Notification Domain (Implicitly completed)
- SaaS Phase 9 — Video Provider Abstraction
- SaaS Phase 10 — Bulk Import

The next implementation phase is **SaaS Phase 11 — Audit Log**.

## Phase 10 acceptance baseline

The repository's latest main commit explicitly records SaaS Phase 10 as completed and identifies SaaS Phase 11 as the next implementation phase.

Accepted Phase 10 decisions:
- Use `openpyxl` for XLSX support rather than adding a heavy data-science dependency.
- Use an all-or-nothing transactional boundary during import commit.
- Fall back to email for a newly created user's username when required by the current account model.
- Fall back to `UTC` when an imported timezone is missing.
- Assign `TEACHER` organization role for teacher imports and `STAFF` for student/parent imports under the current organization-role constraints.
- Do not build arbitrary model importers or a frontend spreadsheet editor in Phase 10.
- Preserve tenant isolation during validation and transaction-safe commit.

## SaaS Phase 11 objective

Build a central, tenant-aware **Audit Log** domain for security, accountability, troubleshooting and operational support.

The audit system must record important academy actions without coupling audit persistence directly to individual business models.

Primary flow:

```text
authorized domain action
        ↓
audit service / audit event
        ↓
immutable audit record
        ↓
academy-scoped query/reporting
```

## Phase 11 non-negotiable rules

1. Audit records are append-only from the application perspective.
2. Existing audit records must not be edited or deleted through normal application APIs.
3. Every tenant-owned audit record belongs to exactly one organization.
4. Audit queries are organization-scoped and permission-checked.
5. A user from Academy A must never read Academy B's audit records, even with a known audit ID.
6. Sensitive values such as passwords, tokens, API keys, credentials and full private file contents must never be stored in audit payloads.
7. Audit payloads should capture safe metadata and before/after summaries only where necessary.
8. Audit logging must not change business transaction outcomes when audit recording is intentionally configured as best-effort for non-security-critical events; security-critical events should fail closed only when the phase specification explicitly requires it.
9. Do not scatter raw `AuditLog.objects.create(...)` calls throughout unrelated views. Use a small service/interface.
10. The audit layer must work with the current organization membership and role model.
11. Do not introduce a second authorization system.
12. Preserve PostgreSQL compatibility and all existing regression behaviour.

## Expected Phase 11 domain

Implement a central model/service capable of recording at least:

- actor user or system actor;
- organization;
- action/event type;
- target object type;
- target object identifier;
- request correlation identifier when available;
- safe structured metadata;
- creation timestamp.

Recommended action categories include:

```text
authentication
membership
permissions
academy_settings
curriculum
student_enrollment
scheduling
assessment
pricing
payout
notification
video
bulk_import
```

Only record events that materially improve accountability. Do not turn every read request into a permanent audit event.

## Required implementation areas

- `auditlog` application or repository-equivalent audit domain.
- Immutable `AuditLog` model/migration.
- Audit service/helper API.
- Organization-scoped permissions/querysets.
- Structured event/action choices.
- Safe metadata redaction/normalization.
- API endpoints for authorized audit-history retrieval.
- Pagination and filtering by action, actor, object and date range.
- Tests for cross-tenant isolation and immutability.
- OpenAPI documentation.
- PostgreSQL phase gate.

## Preferred API shape

Follow existing organization-scoped routing conventions. A suitable starting point is:

```text
GET /api/organizations/<organization_pk>/audit-logs/
GET /api/organizations/<organization_pk>/audit-logs/<id>/
```

Do not expose a global unscoped audit-log endpoint.

Filtering may include:

```text
action
actor
object_type
object_id
created_after
created_before
```

Do not allow clients to bypass tenant ownership by supplying another organization identifier.

## Audit write behaviour

Use a central service similar to:

```text
record_event(
    organization=...,
    actor=...,
    action=...,
    target=...,
    metadata=...,
)
```

The service should:
- normalize actor identity;
- validate organization context;
- derive target information safely;
- remove prohibited sensitive fields;
- create an immutable event record;
- preserve timestamps using timezone-aware datetimes;
- remain reusable from views, domain services and import workflows.

Where a domain operation already runs inside `transaction.atomic()`, attach the audit write to that transaction unless the phase specification explicitly requires an independent outbox/event design.

## Security and privacy

Never store:

```text
passwords
access tokens
refresh tokens
API keys
secret keys
private credentials
raw uploaded files
full authorization headers
```

When recording changes, prefer safe summaries such as:

```text
status: active → suspended
role: teacher → admin
```

rather than full serialized objects.

## Testing gate

The phase is not complete until tests prove at minimum:

- valid audit events are created;
- audit records are tenant-owned;
- Academy A cannot read Academy B audit records by list endpoint;
- Academy A cannot retrieve Academy B audit records by guessed ID;
- unauthorized roles cannot read restricted audit history;
- suspended memberships cannot use audit endpoints;
- ordinary users cannot mutate or delete audit records;
- sensitive fields are redacted or rejected;
- filtering and pagination stay tenant-scoped;
- bulk-import events can be audited;
- existing application tests continue to pass.

Required PostgreSQL gate:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

## Definition of done

Phase 11 is complete only when:

1. The audit model and migration exist.
2. Audit writes go through a reusable service/interface.
3. Important Phase 11 event categories are wired into the highest-value existing workflows without creating excessive noise.
4. Audit reads are organization-scoped and permission-safe.
5. Audit records are immutable through normal application APIs.
6. Sensitive metadata is redacted.
7. Tenant-isolation, permission, immutability and regression tests pass.
8. OpenAPI documents the audit endpoints.
9. PostgreSQL checks and the full test suite pass.
10. The next phase is documented explicitly before implementation moves forward.

## Scope discipline

Do not implement in Phase 11:

- payment gateway work;
- accounting/tax features;
- analytics warehouse pipelines;
- AI scoring;
- frontend application screens beyond API readiness;
- a full event-sourcing architecture;
- a separate database per academy.

The goal is a small, reliable, tenant-safe audit boundary that can support the SaaS platform and future frontend/admin tooling.
 