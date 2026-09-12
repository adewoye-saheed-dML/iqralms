# SaaS Phase 11 — Audit Log

## Status

READY FOR IMPLEMENTATION

## Repository

`adewoye-saheed-dML/quran_acad`

## Target branch

`main`

## Current baseline

SaaS Phases 1–10 are complete in the current repository baseline.

Current verified `main` head:

`52107315cde805be465334970961de5c4116abd7`

Current completed phase:

**SaaS Phase 10 — Bulk Import**

Next phase:

**SaaS Phase 11 — Audit Log**

The product roadmap identifies audit logging as the next important backend trust and support capability after bulk import and before the API contract is frozen for frontend development.

---

# 1. Objective

Introduce a central, tenant-aware audit logging capability for sensitive academy administration and operational actions.

The audit system should answer:

- who performed an important action;
- which academy it affected;
- what action occurred;
- what object was affected;
- when it happened;
- which safe contextual metadata is useful for support or investigation.

The system must be useful for a real SaaS operator without becoming a noisy event store or a second business database.

---

# 2. Why this phase exists

A multi-academy product needs an accountability boundary for:

- permission changes;
- membership changes;
- academy configuration changes;
- curriculum changes;
- enrollment operations;
- pricing changes;
- payout actions;
- bulk imports;
- notification/provider configuration;
- other sensitive administrative operations.

The existing roadmap explicitly places audit logging after bulk import as a P1 platform capability. The implementation should therefore be small, reusable and independent of the domain-specific models that generate events.

---

# 3. Scope

## In scope

- A central `AuditLog` model or repository-equivalent.
- Organization/academy ownership.
- Actor tracking.
- Action/event taxonomy.
- Target object type and identifier.
- Safe structured metadata.
- Timestamping.
- A reusable audit service.
- Organization-scoped audit-history endpoints.
- Pagination.
- Filtering by action, actor, object and date range.
- Permission enforcement using existing organization roles.
- Immutability through normal application APIs.
- Sensitive-field redaction.
- Tests for tenant isolation, authorization, immutability and regression safety.
- OpenAPI documentation.
- PostgreSQL phase gate.
- Documentation of deliberate decisions and deferrals.

## Explicitly out of scope

- Full event-sourcing architecture.
- Message queues or a distributed event bus.
- Long-term archival storage.
- Cross-tenant global audit dashboards exposed to academy users.
- Payment gateway implementation.
- Accounting or tax features.
- Data warehouse pipelines.
- AI analysis of audit history.
- Frontend audit screens beyond API readiness.
- Automatic compliance certification.
- A separate database per academy.

---

# 4. Existing architecture constraints

## 4.1 Tenant boundary

The application is multi-tenant.

Every academy-owned audit event must have a deterministic organization relationship.

Do not create a global audit endpoint that allows the caller to choose an organization without validating membership.

## 4.2 Identity

`accounts.User` remains the global identity model.

The actor's effective authority is determined through `OrganizationMembership` and the existing organization role system.

Do not invent a second role or permission framework for auditing.

## 4.3 Organization roles

Follow the repository's existing organization-role model.

At minimum, audit-history access should be restricted to academy users with the roles already permitted to inspect sensitive administrative activity.

Do not automatically assume that every teacher, student or parent can read the audit history.

## 4.4 Domain independence

Audit logging must not create direct foreign-key dependencies on every business model in the application.

The audit target should be represented through generic/safe object metadata such as:

```text
object_type
object_id
```

or the repository's equivalent generic relation mechanism.

---

# 5. AuditLog model

Use the smallest field set that supports traceability.

Conceptually:

```text
id
organization
actor
actor_type
action
object_type
object_id
metadata
request_id
created_at
```

Possible additional safe fields may include:

```text
ip_address
user_agent
```

only when the existing application privacy/security posture supports retaining them.

Do not collect extra request information merely because it is technically available.

## Immutability

The audit record is append-only.

Normal application APIs must not provide:

```text
PATCH /audit-logs/<id>/
PUT /audit-logs/<id>/
DELETE /audit-logs/<id>/
```

If an internal administrative maintenance command is ever required later, it must be treated as a separate privileged operational capability, not part of Phase 11.

---

# 6. Action taxonomy

Create a finite set of documented action names.

Recommended categories:

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

Examples of concrete events:

```text
membership.created
membership.role_changed
membership.suspended
membership.reactivated
academy.settings_updated
curriculum.track_created
curriculum.level_updated
enrollment.created
enrollment.withdrawn
pricing.agreement_created
pricing.agreement_updated
payout.finalized
notification.provider_updated
video.provider_updated
bulk_import.validated
bulk_import.committed
```

The exact final taxonomy should follow existing repository naming conventions.

Do not create dozens of events that have no operational value.

---

# 7. Audit service

Create one reusable service/interface, for example:

```python
record_event(
    organization=organization,
    actor=request.user,
    action="membership.role_changed",
    target=membership,
    metadata={"old_role": "teacher", "new_role": "admin"},
    request_id=request_id,
)
```

The implementation should:

1. Validate organization ownership.
2. Resolve the actor safely.
3. Resolve target type/id safely.
4. Sanitize metadata.
5. Persist the audit record.
6. Preserve timezone-aware timestamps.

The service should be usable from:

- API views;
- domain services;
- import commit workflows;
- future background jobs.

Do not duplicate serialization and redaction logic in every caller.

---

# 8. Metadata rules

Audit metadata must be structured JSON or the repository's equivalent.

Safe examples:

```json
{
  "old_role": "teacher",
  "new_role": "admin",
  "source": "academy_settings"
}
```

Avoid full model serialization.

Never store:

```text
passwords
access tokens
refresh tokens
API keys
secret keys
credentials
authorization headers
private file bytes
raw uploaded spreadsheets
session cookies
```

The sanitizer should recursively remove or reject prohibited keys where practical.

Potentially sensitive values should be summarized rather than retained verbatim.

---

# 9. Transaction behaviour

When a business action and its audit event are part of the same database transaction, prefer recording the audit event inside that transaction so the event accurately reflects the committed operation.

Example:

```text
transaction.atomic()
    change membership
    record audit event
commit
```

Do not create an audit event claiming success before the underlying business action has committed.

For operations that are intentionally best-effort and not security-critical, document the failure policy instead of silently swallowing errors.

Security-sensitive actions must follow an explicit fail-closed/fail-open decision documented in code and tests.

---

# 10. Organization ownership and tenant isolation

Required invariants:

1. Every tenant audit record belongs to exactly one organization.
2. An actor must be authorized in that organization when recording a user action.
3. Academy A cannot list Academy B audit records.
4. Academy A cannot retrieve Academy B audit records by known ID.
5. Organization IDs supplied by clients cannot override authenticated membership context.
6. A target object from Academy B must not be recorded as if it belonged to Academy A.
7. Cross-academy target relationships must be rejected.
8. Suspended memberships cannot use restricted audit-history endpoints.
9. Student/parent access must remain limited according to the repository's role policy.
10. Filters must never widen the tenant scope.

Tenant isolation must be enforced in:

```text
querysets/services
permissions
validation
API tests
```

not only in serializers.

---

# 11. Audit read API

Prefer organization-scoped routes consistent with the rest of the SaaS API.

Recommended:

```text
GET /api/organizations/<organization_pk>/audit-logs/
GET /api/organizations/<organization_pk>/audit-logs/<id>/
```

The list endpoint should support safe filtering such as:

```text
action
actor
object_type
object_id
created_after
created_before
```

Pagination is required.

Return structured records rather than raw database fields that are not needed by clients.

Do not expose internal database implementation details.

---

# 12. Response design

A useful record shape is:

```json
{
  "id": 101,
  "organization": 7,
  "actor": 25,
  "action": "membership.role_changed",
  "object_type": "organization_membership",
  "object_id": "44",
  "metadata": {
    "old_role": "teacher",
    "new_role": "admin"
  },
  "request_id": "req_abc123",
  "created_at": "2026-09-12T12:00:00Z"
}
```

Exact field names may follow existing serializer/API conventions.

---

# 13. High-value integrations

Phase 11 should prioritize a small number of meaningful workflows.

At minimum, consider wiring audit events for:

- organization membership creation and role changes;
- membership suspension/reactivation;
- academy settings changes;
- curriculum administrative mutations;
- pricing agreement administrative mutations;
- teacher payout finalization or similarly sensitive payout actions;
- notification/video provider configuration changes;
- bulk import validation and commit.

Do not instrument every GET endpoint.

Do not produce a separate audit entry for every row processed internally by a large bulk operation unless there is a clear operational need. Prefer a summary event for the import job and preserve row-level detail inside the import result domain.

---

# 14. Bulk import integration

The completed Phase 10 import system should generate useful audit events without exposing its raw source file.

Recommended events:

```text
bulk_import.validated
bulk_import.committed
```

Safe metadata may include:

```text
import_job_id
kind
row_count
valid_row_count
invalid_row_count
created_count
updated_count
skipped_count
error_count
```

Do not copy the spreadsheet rows into the audit record.

---

# 15. Immutability enforcement

The simplest safe design is:

- no write serializers for audit records;
- no mutation endpoints;
- database row creation only through the audit service or controlled model creation path;
- read-only serializer/queryset for application users.

Tests should explicitly attempt modification and deletion through the API and prove those operations are unavailable or rejected.

---

# 16. Privacy and retention

Phase 11 should minimize stored information.

Do not add a complex retention framework unless the existing project requirements demand it.

Document that audit records are operational/security records and that future retention/deletion policy can be implemented as a separate controlled capability.

Avoid storing duplicate full copies of domain objects.

---

# 17. Testing requirements

## Model/service tests

Prove:

- event creation works;
- organization is required;
- target metadata is stored correctly;
- timestamps are timezone-aware;
- sensitive metadata is removed/rejected;
- audit service rejects invalid cross-organization targets.

## Tenant isolation tests

Prove:

```text
Academy A cannot list Academy B audit records.
Known audit ID from B is rejected.
A cannot filter into B's audit history.
A cannot record an Academy B target as its own event.
```

## Permission tests

Prove:

- authorized academy roles can view allowed audit history;
- unauthorized roles are rejected;
- suspended members are rejected;
- object-level access is still tenant-scoped.

## Immutability tests

Prove:

```text
PATCH audit record -> rejected
PUT audit record -> rejected
DELETE audit record -> rejected
```

## Sensitive-data tests

At minimum test that:

```text
password
access_token
refresh_token
api_key
secret_key
authorization
```

are not persisted in audit metadata.

## Integration tests

Prove audit events are emitted for the selected high-value workflows, including bulk-import commit.

## Regression tests

All existing:

```text
accounts
curriculum
scheduling
pricing
assessment
payouts
notifications
video-provider
imports
```

tests must remain green.

---

# 18. PostgreSQL phase gate

Run against PostgreSQL:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

The phase is not complete based only on local SQLite or endpoint smoke tests.

---

# 19. OpenAPI requirements

Document the audit read endpoints in the project's existing OpenAPI generation mechanism.

The schema should describe:

- organization-scoped path parameters;
- pagination;
- filtering;
- response fields;
- authorization expectations;
- expected forbidden/not-found behaviour for cross-tenant access.

Do not expose write endpoints for audit records.

---

# 20. Definition of done

Phase 11 is accepted only when all of the following are true:

- Audit model/migration exists.
- Audit service exists and is reusable.
- Audit records are organization-scoped.
- Sensitive metadata is redacted.
- High-value administrative workflows emit events.
- Bulk-import operations can be traced without storing raw spreadsheet data.
- Audit list/detail APIs are organization-scoped.
- Pagination and filtering are implemented safely.
- Audit mutation/deletion is unavailable to normal application users.
- Tenant-isolation tests pass.
- Permission tests pass.
- Immutability tests pass.
- Existing regression tests pass.
- PostgreSQL gate passes.
- OpenAPI is updated.
- Deliberate deferrals are documented.

Only after these conditions are met should the project move to the next phase, which should focus on the final frontend-facing API contract and documentation freeze rather than adding unrelated product features.

---

# 21. Deliberate deferrals

Do not implement in Phase 11:

- a complete event-sourcing architecture;
- Kafka/RabbitMQ or another distributed event bus;
- automated compliance certifications;
- payment processing;
- accounting/tax modules;
- analytics warehouse pipelines;
- AI-based anomaly detection;
- a full audit-log frontend;
- automatic cross-tenant platform dashboards.

Keep Phase 11 small, secure, tenant-safe and operationally useful.
