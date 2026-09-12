# SaaS Phase 11 — Audit Trail Completion, Hardening & Workflow Integration

## Status

**READY FOR IMPLEMENTATION / COMPLETION**

This specification is intentionally written against the **current live repository**, not against an assumed pre-Phase-11 state.

Repository: `adewoye-saheed-dML/quran_acad`  
Target branch: `main`  
Current main head inspected: `763ec118c3d42a0b12e116ea1d5f3ea04567c712`  
Current main commit message: `feat(audit):phase 11`

The repository already contains an `audit_logs` implementation and the current `main` also contains some membership audit integration. Therefore this phase is **not** a greenfield audit-log build. It is the completion, hardening, security verification, and systematic integration phase required to make the audit trail genuinely useful across the SaaS.

---

# 1. Executive objective

Deliver a **usable, tenant-safe, append-only audit trail** that:

1. records important business/security actions automatically;
2. preserves which academy the event belongs to;
3. identifies the human or system actor;
4. captures the affected object;
5. records safe, structured metadata;
6. supports request/correlation tracing;
7. prevents ordinary application mutation/deletion;
8. is readable only by authorized academy roles;
9. cannot be bypassed through filters, IDs, or cross-tenant target objects;
10. records high-value changes across the existing SaaS domains;
11. audits Phase 10 bulk imports without storing source-file contents;
12. remains PostgreSQL-compatible and regression-safe;
13. leaves a documented boundary for the next phase.

The final deliverable is **not** “an AuditLog table and two GET endpoints.”

The final deliverable is:

> **An automatically populated, tenant-safe accountability trail for important SaaS operations.**

---

# 2. Current repository findings

The implementation must begin by validating the repository before changing code.

## 2.1 Existing audit app

The active implementation is in:

```text
audit_logs/
```

The root settings currently register:

```python
"audit_logs",
```

and the root URL configuration currently includes:

```python
path("api/organizations/", include("audit_logs.urls")),
```

Therefore the current standard must be to continue with **`audit_logs`**, unless a repository-wide inspection proves there is a strong reason to rename it.

## 2.2 Duplicate `audit/` app

The repository also contains:

```text
audit/
```

That directory is a second Django app scaffold with its own:

```text
apps.py
admin.py
models.py
tests.py
views.py
migrations/
```

It is not currently registered in `INSTALLED_APPS`.

The implementation must not blindly delete it.

Before touching it, verify:

```text
INSTALLED_APPS
imports
URL includes
migration history
Git references
test references
management commands
documentation
```

Then standardize on one audit domain.

**Preferred outcome:** keep `audit_logs/` because it already owns the working implementation and is registered in settings; remove the unused `audit/` scaffold only after confirming that the repository contains no live dependency on it.

If the duplicate is removed, remove only the dead app, not the actual `audit_logs` implementation.

---

# 3. Current implementation baseline

The current `audit_logs.models.AuditLog` already contains:

```text
organization
actor
action
object_type
object_id
metadata
request_id
created_at
```

and uses:

```python
save()
delete()
```

guards to prevent normal instance updates/deletes.

The current service already:

- validates an actor's active membership;
- rejects cross-organization targets where a target exposes `organization` / `organization_id`;
- recursively sanitizes selected sensitive metadata keys;
- records through `AuditLog.objects.create()`.

The current read API already provides:

```text
GET /api/organizations/<organization_pk>/audit-logs/
GET /api/organizations/<organization_pk>/audit-logs/<id>/
```

with filters for:

```text
action
actor
object_type
object_id
created_after
created_before
```

and pagination.

The current membership domain already emits audit events for membership creation and membership state/role changes.

These existing capabilities must be **hardened and completed**, not duplicated.

---

# 4. Current gaps that Phase 11 must close

The current repository still requires explicit work in the following areas.

## 4.1 Broad workflow integration

Membership changes are already partly wired.

The remaining work is to systematically audit the existing domain surface and wire only the highest-value mutations.

At minimum investigate:

```text
organizations
accounts / authentication
curriculum
scheduling
assessment
pricing
payouts
notifications
video/provider configuration
imports
```

The implementation must not assume that because a model exists, it should be audited.

Audit **business mutations with accountability value**, not every ORM write and not every read request.

## 4.2 Controlled action vocabulary

`action` is currently a free-form `CharField(max_length=128)`.

A random string such as:

```text
hello
abc123
test.action
```

can therefore technically be stored.

Phase 11 must introduce a controlled vocabulary.

Use a repository-friendly implementation such as:

- `TextChoices`;
- immutable event constants;
- a central `AuditAction` namespace;
- or a combination of those.

Do not scatter literal strings throughout the repository.

---

# 5. Required final action taxonomy

Use a small, explicit taxonomy.

## Authentication

```text
auth.login_success
auth.login_failed
auth.logout
auth.password_changed
auth.password_reset_requested
```

Only implement events that the current authentication architecture can observe reliably.

Do not invent an authentication redesign merely to generate logs.

## Organization / membership

```text
organization.created
organization.settings_updated

membership.created
membership.role_changed
membership.suspended
membership.reactivated
```

If the current system has no invitation lifecycle, do not fabricate:

```text
membership.invited
membership.accepted
```

as first-class business events until there is an actual invitation workflow to instrument.

## Permissions

```text
permission.changed
```

Use only where the application has a meaningful permissions mutation separate from membership role changes.

Do not introduce a second authorization system.

## Curriculum

Use the actual tenant-scoped curriculum mutation surface.

Examples:

```text
curriculum.track_created
curriculum.track_updated
curriculum.track_archived

curriculum.level_created
curriculum.level_updated
curriculum.level_archived

curriculum.teacher_assignment_changed
```

Use the real repository nouns and existing models.

## Student / enrollment

```text
enrollment.created
enrollment.updated
enrollment.withdrawn
```

Only use events that correspond to actual implemented flows.

## Scheduling

```text
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
```

Do not audit ordinary availability reads.

## Assessment

```text
assessment.created
assessment.updated
assessment.finalized
assessment.reopened
```

Only include finalization/reopening if the existing assessment domain has those state transitions.

## Pricing

```text
pricing.agreement_created
pricing.agreement_updated
pricing.agreement_deactivated
pricing.rate_changed
```

Use actual repository operations.

## Payouts

```text
payout.created
payout.finalized
payout.reopened
```

Use the existing payout lifecycle and preserve existing payout immutability rules.

Never include sensitive payment credentials or full financial secrets in metadata.

## Notifications

```text
notification.provider_updated
notification.configuration_updated
notification.sent
```

Do not record every individual delivery attempt as an audit event unless the existing business requirement clearly needs it. Delivery history belongs to the notification domain.

## Video

```text
video.provider_updated
video.meeting_created
video.meeting_updated
video.meeting_cancelled
```

Do not audit every participant join/read operation.

## Bulk import

```text
bulk_import.started
bulk_import.validated
bulk_import.completed
bulk_import.failed
```

Use whichever states map to the existing `ImportJob` lifecycle.

---

# 6. Event naming rules

Event names must be:

- lowercase;
- dot-separated;
- domain-oriented;
- action-oriented;
- stable once released.

Example:

```text
membership.role_changed
```

Preferred over:

```text
ROLE_CHANGE
membershipRoleChanged
roleChange
membership.updated.role
```

Do not reuse one event name to mean materially different actions.

---

# 7. Audit model requirements

The model must remain small.

Required conceptual fields:

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

## 7.1 Organization

Every tenant event must belong to exactly one organization.

Review the current:

```python
organization = ForeignKey(..., on_delete=models.CASCADE)
```

This is a critical hardening point.

An append-only audit trail must not disappear merely because an organization row is deleted.

Preferred direction:

```python
on_delete=models.PROTECT
```

unless the project deliberately adopts a different immutable-history design.

The chosen policy must be documented.

## 7.2 Actor

The current `actor` field is nullable for system actions.

Keep nullable actor support, but remove ambiguity.

Introduce an explicit actor classification such as:

```text
USER
SYSTEM
```

The design must distinguish:

```text
system-generated event
```

from:

```text
user actor no longer resolvable
```

If the existing `User` can be deleted, retain enough immutable actor identity to preserve audit meaning, for example:

```text
actor_type
actor_id_snapshot
```

Optionally retain a safe display snapshot if justified by the project's privacy policy.

Do not store password credentials.

## 7.3 Object target

Retain a generic target representation:

```text
object_type
object_id
```

Do not add a foreign key from `AuditLog` to every business model.

## 7.4 Timestamp

Use:

```python
DateTimeField(auto_now_add=True)
```

with:

```text
USE_TZ = True
```

and preserve UTC storage.

---

# 8. Action validation

Centralize action validation.

Bad:

```python
record_event(..., action="anything")
```

Preferred:

```python
record_event(
    ...,
    action=AuditAction.MEMBERSHIP_ROLE_CHANGED,
)
```

The service should reject unknown action values.

Tests must prove unknown actions cannot be persisted.

---

# 9. Central audit service contract

Keep one canonical write interface.

Suggested contract:

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

The service is responsible for:

1. actor validation;
2. organization validation;
3. target tenancy validation;
4. action validation;
5. target normalization;
6. metadata sanitization;
7. request ID normalization;
8. append-only persistence.

No domain view should call:

```python
AuditLog.objects.create(...)
```

directly.

If direct model creation is detected during repository review, replace it with the service unless there is a documented exceptional reason.

---

# 10. Transaction semantics

The audit event must describe the business operation accurately.

Preferred pattern:

```python
with transaction.atomic():
    mutate_business_state()
    record_event(...)
```

Do not create:

```text
SUCCESS
```

events before a transactional business mutation succeeds.

For a workflow that can have multiple internal operations, record one summary audit event after the meaningful operation has completed.

## 10.1 Failure policy

Do not make a blanket global decision that every audit failure either:

- always fails the business action; or
- is always swallowed.

Use explicit policy by event class.

Recommended:

### Security-critical events

Examples:

```text
auth.password_changed
membership.role_changed
membership.suspended
permission.changed
```

Prefer fail-closed where losing the audit record would compromise required accountability.

### Operational/non-critical events

Examples:

```text
video.meeting_created
notification.sent
bulk_import.validated
```

May be best-effort if the repository documents the decision and the failure does not produce false success semantics.

The implementation must not silently swallow exceptions.

---

# 11. Request correlation

The current model has `request_id`, but the current service accepts it as a plain string.

That is insufficient for a production audit trail.

Implement a small request-correlation mechanism.

## 11.1 Header

Use:

```text
X-Request-ID
```

unless the application already has a canonical request/correlation header.

## 11.2 Middleware

Add a lightweight middleware that:

1. reads the incoming request ID if present;
2. validates and bounds its length;
3. otherwise generates a new opaque ID;
4. attaches it to request state;
5. exposes it to response headers;
6. lets the audit service retrieve it without every caller manually passing it.

Do not trust client-provided IDs as authentication or authorization data.

Do not put secrets into the request ID.

## 11.3 Service convenience

Prefer allowing:

```python
request_id=None
```

to resolve through a request-context helper when available.

Still permit explicit IDs for:

```text
background jobs
management commands
scheduled work
system events
tests
```

---

# 12. Metadata sanitization policy

The current recursive sanitizer is a useful starting point, but the key list must be expanded and centrally documented.

At minimum cover variants such as:

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

set_cookie
```

Case differences and nested structures must be handled.

Example:

```json
{
  "outer": {
    "credentials": {
      "password": "secret"
    }
  }
}
```

must never persist the secret value.

## 12.1 Redact versus reject

Use **redaction** for expected structured metadata where a secret-looking key can be safely replaced:

```text
[REDACTED]
```

Reject obviously unsafe audit payloads when the input is so large or opaque that safe redaction cannot be guaranteed.

Document the policy.

## 12.2 Value-level risk

Do not attempt to perform unreliable “secret detection” across every string.

Instead:

- prohibit known secret-bearing keys;
- avoid raw object serialization;
- use explicit allowlisted metadata for high-risk integrations;
- never serialize request headers wholesale;
- never serialize uploaded file contents.

## 12.3 Uploaded files

For imports, log:

```text
job ID
kind
row counts
result counts
status
```

Never log:

```text
CSV contents
XLSX contents
binary file data
raw row payloads
```

---

# 13. Before/after change summaries

Where an operation changes important state, metadata should explain the delta.

Preferred:

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

or:

```json
{
  "changes": {
    "status": {
      "from": "active",
      "to": "suspended"
    }
  }
}
```

Avoid:

```json
{
  "before": "<entire serialized model>",
  "after": "<entire serialized model>"
}
```

## 13.1 Change metadata helper

Create a small reusable helper such as:

```python
build_change_summary(
    before={...},
    after={...},
    fields=[...],
)
```

or an equivalent repository-idiomatic function.

It must:

- compare only explicitly selected fields;
- omit unchanged fields;
- sanitize values;
- remain JSON serializable;
- avoid dumping entire model objects.

---

# 14. Existing-domain integration matrix

The implementer must inspect each domain and record the actual mutation points before wiring.

## 14.1 Organizations

Audit:

```text
organization creation
organization settings updates
```

At minimum include:

```text
name
timezone
video provider
active/inactive state
```

only when those fields are actually mutable.

Do not create audit events for plain GETs.

## 14.2 Membership

Already partly integrated.

Verify:

```text
membership.created
membership.role_changed
membership.suspended
membership.reactivated
```

Then verify:

- transactional placement;
- request ID;
- actor;
- before/after data;
- action constants;
- tests.

If invitations are not yet a real stateful workflow, defer invitation-specific events.

## 14.3 Accounts/authentication

Inspect:

```text
register
login
logout
password change
password reset
```

Add events only where the current auth stack provides a reliable hook.

For failed login:

- do not include the submitted password;
- do not include tokens;
- do not log full request bodies.

Safe metadata may include:

```text
auth method
failure reason code
username/email identifier only if policy permits
```

Use care around PII.

## 14.4 Curriculum

Audit important administrative mutations such as:

```text
track creation/update/archive
level creation/update/archive
teacher-track assignment changes
placement state changes where operationally significant
```

Use actual repository model names and state transitions.

## 14.5 Scheduling

Audit:

```text
availability mutation
booking creation
booking update
booking cancellation
booking completion/no-show
cohort mutation
routing/assignment change
```

Do not create audit events for every internal eligibility query.

## 14.6 Assessment

Audit important write transitions:

```text
assessment created
assessment updated
assessment finalized
assessment reopened
```

Only if those operations exist.

Never log sensitive assessment content more broadly than needed.

## 14.7 Pricing

Audit:

```text
pricing agreement creation
pricing agreement update
deactivation
rate changes
```

Do not duplicate the entire agreement record.

Do not log secret payment-provider credentials.

## 14.8 Payouts

Audit sensitive state transitions such as:

```text
payout created
payout finalized
payout reopened/corrected
```

Preserve existing immutable payout behavior.

Do not store bank credentials or secret payment information.

## 14.9 Notifications

Audit meaningful administrative/provider changes:

```text
provider configured
provider updated
configuration changed
message sent
```

Do not make audit history a second delivery-attempt database.

## 14.10 Video

Audit:

```text
provider changes
meeting creation
meeting changes
meeting cancellation
```

Never log provider credentials or access tokens.

## 14.11 Bulk imports

Audit the import lifecycle.

Preferred:

```text
bulk_import.started
bulk_import.validated
bulk_import.completed
bulk_import.failed
```

Safe metadata:

```json
{
  "import_job_id": 42,
  "kind": "students",
  "row_count": 250,
  "valid_row_count": 240,
  "invalid_row_count": 10,
  "created_count": 220,
  "reused_count": 20
}
```

Do not include raw source rows.

The audit event must itself be tenant-safe.

---

# 15. Bulk import failure semantics

The existing Phase 10 import service has an all-or-nothing transaction for the business commit.

The audit design must preserve this.

Preferred semantics:

### Import started

Record when the import begins, but be explicit whether this occurs before or after validation metadata is accepted.

### Import validated

Record:

```text
VALIDATED
```

when validation completes successfully enough to move the job forward.

### Import completed

Record only after the transactional import commit succeeds.

### Import failed

Record a safe failure event after failure handling is complete.

Do not store full stack traces or raw file contents in audit metadata.

A short stable error code/category may be recorded.

---

# 16. Read API authorization

The current API uses:

```text
IsAuthenticated
+
CanManageOrganizationMemberships
```

That is a reasonable baseline because owner/admin are the existing sensitive administrative roles.

The final implementation must explicitly confirm the role policy.

Required negative cases:

```text
teacher -> denied
student -> denied
parent -> denied
suspended member -> denied
non-member -> denied
anonymous -> denied
```

Positive cases:

```text
owner -> allowed
admin -> allowed
```

Do not add a new audit-specific role framework.

---

# 17. Cross-tenant isolation

The isolation model must remain:

```text
authenticated caller
      ↓
organization URL
      ↓
active membership for that exact organization
      ↓
tenant-scoped queryset
      ↓
target lookup
```

Never:

```text
organization ID from request body
      ↓
trust
```

## 17.1 Known-ID attack

Test:

```text
Academy A owner
    ↓
GET /organizations/B/audit-logs/<known-id>/
```

Expected result:

```text
404 or the repository's deliberately chosen non-disclosure response
```

Do not return 403 for tenant object lookups when doing so reveals the existence of the record and contradicts existing tenant conventions.

## 17.2 Filter attack

Test:

```text
?actor=<user from B>
?object_id=<object from B>
?action=<valid action>
```

The query must remain constrained to Academy A.

A filter must never widen the root queryset.

---

# 18. Filter validation

Current filters include:

```text
action
actor
object_type
object_id
created_after
created_before
```

Harden them.

## 18.1 Action

Must accept only valid action constants.

## 18.2 Actor

Must not cause a cross-tenant lookup leak.

A malformed actor ID should return a safe validation response rather than a server error.

## 18.3 Dates

Parse ISO 8601 values explicitly.

Reject malformed timestamps cleanly.

Use timezone-aware datetimes.

Define whether:

```text
created_before
```

is inclusive or exclusive and document it.

## 18.4 Object type/id

Treat them as strings, bounded in length.

Do not allow arbitrary query syntax.

---

# 19. Pagination

Current intended limits:

```text
page_size = 50
max_page_size = 1000
```

Keep or improve these limits without creating unnecessary API breakage.

Required tests:

```text
page 1
page 2
page_size
max page size
stable ordering
tenant isolation across pages
```

Ordering must remain deterministic, e.g.:

```text
-created_at
-id
```

so two events with the same timestamp do not randomly swap order.

---

# 20. Query/index strategy

Audit logs are write-heavy and read by tenant/date/action.

Add indexes that reflect actual query patterns.

Recommended candidates:

```text
(organization, -created_at)
(organization, action, -created_at)
(organization, actor, -created_at)
(organization, object_type, object_id, -created_at)
```

Use the database and actual query patterns to decide the final minimal set.

Do not add indexes merely because every field could theoretically be filtered.

Run query inspection where useful.

---

# 21. Immutability — application layer

The current `save()` / `delete()` protections are not sufficient by themselves because Django querysets can bypass overridden instance methods.

Specifically investigate:

```python
AuditLog.objects.filter(...).update(...)
AuditLog.objects.filter(...).delete(...)
```

and any manager methods that permit writes.

## 21.1 Application requirements

Normal application paths must have no way to:

```text
PATCH
PUT
DELETE
```

audit records.

The serializer should be read-only.

No mutation view should exist.

Django admin must not permit editing/deletion.

## 21.2 Database-level protection

Because PostgreSQL is the canonical database, implement a PostgreSQL-level guard if practical.

Preferred design:

- allow INSERT;
- reject UPDATE;
- reject DELETE.

Implement via a migration using a PostgreSQL trigger/function.

Document the trigger clearly.

The reverse migration must remove the trigger cleanly.

This is the strongest protection against accidental ORM-side mutation.

Do not claim that an application trigger prevents a superuser or privileged DBA from altering data. The requirement is normal application immutability, not cryptographic tamper-proofing.

---

# 22. Admin interface

Choose one of two deliberate outcomes.

### Preferred

Expose audit logs in Django admin as:

```text
read-only
```

with:

- list display;
- filters;
- search;
- no add;
- no change;
- no delete.

The admin must reinforce the append-only rule.

### Alternative

Do not register the model at all.

Either choice is acceptable only if documented.

Do not leave a misleading admin registration that appears editable.

---

# 23. Serializer requirements

The audit serializer must be read-only.

Fields should be:

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

Only expose fields that are intended for clients.

Do not expose internal model state merely because it exists.

Do not expose:

```text
raw SQL information
database internals
private server paths
full traceback strings
provider credentials
raw request headers
```

---

# 24. OpenAPI requirements

The schema must describe:

## List endpoint

```text
GET /api/organizations/{organization_pk}/audit-logs/
```

with:

- organization path parameter;
- pagination;
- action filter;
- actor filter;
- object type/id filters;
- date filters.

## Detail endpoint

```text
GET /api/organizations/{organization_pk}/audit-logs/{id}/
```

Document:

```text
200
401
403
404
```

according to actual behavior.

There must be no audit write endpoint in the public API.

---

# 25. Testing plan

Tests are a first-class deliverable.

## 25.1 Model tests

Test:

```text
record can be created
organization is required
actor can be null for system event
actor type is valid
known action is accepted
unknown action is rejected
created_at is timezone-aware
target metadata is correct
```

## 25.2 Service tests

Test:

```text
active actor accepted
non-member rejected
suspended actor rejected
system actor accepted where allowed
cross-tenant target rejected
target with no organization handled safely
metadata recursively sanitized
request id preserved
```

## 25.3 Immutability tests

Attempt:

```text
instance.save()
instance.delete()

QuerySet.update()
QuerySet.delete()

serializer update
API PATCH
API PUT
API DELETE
```

Expected:

```text
rejected/unavailable
```

Also verify the record is unchanged after failed attempts.

## 25.4 Permission tests

Required matrix:

| Actor | Expected |
|---|---|
| Owner | Allow |
| Admin | Allow |
| Staff | Deny unless policy explicitly changes |
| Teacher | Deny |
| Student | Deny |
| Parent | Deny |
| Suspended member | Deny |
| Non-member | Deny |
| Unauthenticated | Deny |

The test suite must reflect the final documented policy.

## 25.5 Tenant isolation tests

Test:

```text
A list cannot see B
A detail cannot see B
A known B ID cannot be read
A's filters cannot expose B
A cannot record B target
A cannot supply B organization ID to widen writes
```

## 25.6 Filter tests

Test every supported filter individually and in combinations.

At minimum:

```text
action
actor
object_type
object_id
created_after
created_before
```

with tenant isolation.

## 25.7 Pagination tests

Test:

```text
default page size
custom page size
max page size
page 1
page 2
stable ordering
cross-tenant isolation across pages
```

## 25.8 Redaction tests

At minimum:

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

Test:

- top-level keys;
- nested dictionaries;
- nested lists;
- mixed-case keys.

## 25.9 Request ID tests

Test:

```text
header supplied -> same bounded ID appears on audit event
header missing -> generated ID
malformed/oversized ID -> safe handling
system/background event -> explicit request ID may be supplied
response includes correlation ID if that is the chosen contract
```

## 25.10 Integration tests

At minimum cover:

```text
organization creation/settings mutation
membership create/role/suspend/reactivate
selected curriculum mutation
selected scheduling mutation
selected assessment mutation
selected pricing mutation
selected payout mutation
selected notification mutation
selected video mutation
bulk import lifecycle
authentication events supported by the current architecture
```

Do not test hypothetical workflows that do not exist in the repository.

---

# 26. Integration rule: audit the mutation boundary, not the model

When reviewing a domain, find the **authoritative service/view/workflow** that performs the mutation.

Put the audit call there.

Do not add signals merely because they are convenient unless the repository has a clear signal-based architecture.

Avoid auditing from:

```python
Model.save()
post_save
pre_save
```

for generic domain-wide event creation, because that tends to:

- generate noise;
- lose actor/request context;
- fire for internal operations;
- make before/after information harder to interpret;
- create recursion hazards;
- make transaction semantics less obvious.

Prefer explicit business-service integration.

---

# 27. Before/after integration pattern

For an update operation:

```python
old_values = {
    "role": membership.role,
    "status": membership.status,
}

with transaction.atomic():
    membership = serializer.save()

    changes = build_change_summary(
        before=old_values,
        after={
            "role": membership.role,
            "status": membership.status,
        },
        fields=["role", "status"],
    )

    if changes:
        record_event(
            organization=organization,
            actor=request.user,
            action=AuditAction.MEMBERSHIP_ROLE_CHANGED,
            target=membership,
            metadata={"changes": changes},
        )
```

Do not invent `role_changed` when only `status` changed.

Select the event from the actual transition.

---

# 28. Audit event volume rules

Audit logs are not debug logs.

Do not record:

```text
GET requests
list reads
serializer validation reads
background polling
every provider HTTP call
every ORM query
every row in a bulk file
```

Do record:

```text
authorization changes
security events
membership mutations
important configuration changes
financial state transitions
meaningful scheduling changes
import lifecycle
provider configuration changes
```

The final event set must be operationally useful.

---

# 29. Sensitive privacy boundary

Audit metadata must not become a shadow database.

Never store:

```text
passwords
password hashes
reset tokens
access tokens
refresh tokens
API keys
provider secrets
full authorization headers
cookies
session secrets
private keys
raw uploaded files
raw spreadsheet contents
unbounded stack traces
```

Use stable codes instead:

```json
{
  "failure_code": "INVALID_TIMEZONE"
}
```

instead of:

```json
{
  "exception": "entire internal traceback..."
}
```

---

# 30. System actor conventions

System events must use:

```text
actor_type = SYSTEM
actor = null
```

User events:

```text
actor_type = USER
actor = authenticated user
```

If actor provenance is lost because the user is later deleted, the immutable snapshot fields must preserve the audit identity required by the project policy.

Document this explicitly.

---

# 31. Organization deletion policy

Before accepting the phase as complete, decide what happens if an organization is deleted.

Recommended:

```text
audit logs prevent organization deletion
```

through the database relation:

```text
PROTECT
```

This preserves the accountability history.

If a different retention policy is adopted, document:

- why;
- what is retained;
- what is lost;
- how that affects audit guarantees.

Do not silently keep `CASCADE` if the stated requirement is immutable history.

---

# 32. Duplicate app resolution procedure

Run repository-wide checks for:

```text
audit.
audit_logs.
AuditLog
AuditConfig
AuditLogsConfig
```

Check:

```text
INSTALLED_APPS
urls.py
migrations
imports
tests
documentation
```

Then make one explicit decision.

Preferred final state:

```text
audit_logs/   -> active audit domain
audit/        -> removed as dead scaffold
```

provided verification proves `audit/` is unused.

---

# 33. PostgreSQL compatibility

The repository explicitly treats PostgreSQL as canonical.

Do not use SQLite-specific behavior for Phase 11.

The final phase gate must run on PostgreSQL.

Required commands:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Also run the project's actual PostgreSQL test environment/configuration, not merely a local fallback configuration.

---

# 34. Migration requirements

Every schema change must have a migration.

Potential changes include:

```text
action choice enforcement
actor_type
actor_id_snapshot
organization on_delete policy
indexes
immutable trigger
request/correlation support if persisted differently
```

Migration standards:

- deterministic;
- reversible where practical;
- PostgreSQL-safe;
- no data-destructive shortcuts;
- inspect generated SQL when introducing database triggers.

If existing rows would violate a new invariant, provide a safe data migration or preserve the current valid values.

---

# 35. Documentation requirements

Before declaring completion, update:

```text
CLAUDE.md
specs/saas/SaaS Phase 11 — Audit Log.md
learnings.md
tech-debt.md
OpenAPI/schema output if repository tracks it
```

Document:

- final event taxonomy;
- actor/system conventions;
- request ID behavior;
- metadata redaction policy;
- organization deletion policy;
- immutability mechanism;
- admin decision;
- best-effort/fail-closed policy;
- deliberately deferred events;
- next phase.

---

# 36. Suggested implementation sequence

## 11.0 — Repository audit

Inspect:

```text
audit/
audit_logs/
organizations/
accounts/
curriculum/
scheduling/
assessment/
pricing/
payouts/
notifications/
imports/
config/
```

Build a mutation inventory.

Do not code yet.

## 11.1 — Consolidate audit domain

- resolve duplicate `audit/` vs `audit_logs/`;
- standardize imports;
- confirm migrations;
- confirm URL registration;
- confirm app configuration.

## 11.2 — Audit domain hardening

Implement:

- action taxonomy;
- actor type;
- actor snapshot as required;
- organization deletion protection;
- indexes;
- immutable serializer;
- read-only admin.

## 11.3 — Service hardening

Implement:

- action validation;
- strict actor handling;
- target validation;
- change-summary helper;
- expanded sanitizer;
- request ID resolution.

## 11.4 — Request correlation

Implement middleware/helper and test propagation.

## 11.5 — Read API hardening

Verify:

- authorization;
- tenant queryset;
- 404 behavior;
- filtering;
- date parsing;
- pagination;
- deterministic ordering;
- OpenAPI.

## 11.6 — Database immutability

Add the PostgreSQL update/delete trigger and migration.

## 11.7 — Workflow integration

Integrate the highest-value mutation boundaries domain by domain.

Start with:

```text
organizations
membership
imports
payouts
pricing
scheduling
assessment
curriculum
notifications
video
auth
```

Use the actual repository's mutation paths.

## 11.8 — Bulk import audit

Instrument the Phase 10 import lifecycle with summary metadata only.

## 11.9 — Adversarial tests

Run:

```text
cross-tenant
known-ID
filter
role
suspension
mutation
redaction
request-ID
pagination
```

attacks.

## 11.10 — Full regression

Run all repository tests.

## 11.11 — PostgreSQL gate

Run the full PostgreSQL gate and migration checks.

## 11.12 — Documentation closure

Update:

```text
CLAUDE.md
learnings.md
tech-debt.md
Phase 11 spec
OpenAPI
```

Then explicitly identify the next phase.

---

# 37. Definition of done

Phase 11 is complete only when all of the following are true.

## Domain

- [ ] one canonical audit application exists;
- [ ] duplicate `audit/` scaffold has been deliberately resolved;
- [ ] action vocabulary is controlled;
- [ ] organization ownership is mandatory;
- [ ] actor/system semantics are explicit;
- [ ] target identity is captured;
- [ ] timestamps are timezone-aware;
- [ ] useful indexes exist.

## Security

- [ ] actor must be valid for the organization;
- [ ] suspended members cannot use audit read APIs;
- [ ] unauthorized roles are denied;
- [ ] cross-tenant target writes fail;
- [ ] cross-tenant reads fail safely;
- [ ] known IDs do not bypass tenancy;
- [ ] filters cannot widen tenant scope;
- [ ] sensitive keys are redacted;
- [ ] raw uploaded files are never logged;
- [ ] authentication metadata does not leak credentials.

## Immutability

- [ ] instance update blocked;
- [ ] instance delete blocked;
- [ ] queryset update blocked;
- [ ] queryset delete blocked;
- [ ] write serializers do not exist;
- [ ] write endpoints do not exist;
- [ ] admin cannot edit/delete;
- [ ] PostgreSQL trigger protects update/delete if implemented;
- [ ] organization deletion cannot silently cascade away history.

## Correlation

- [ ] request ID is generated or propagated;
- [ ] audit events can capture it;
- [ ] background/system actions can supply explicit IDs.

## Integration

- [ ] organization mutations audited;
- [ ] membership mutations audited;
- [ ] high-value curriculum mutations audited where applicable;
- [ ] high-value scheduling mutations audited where applicable;
- [ ] high-value assessment mutations audited where applicable;
- [ ] high-value pricing mutations audited where applicable;
- [ ] payout actions audited;
- [ ] notification/provider changes audited where applicable;
- [ ] video/provider changes audited where applicable;
- [ ] bulk import lifecycle audited;
- [ ] supported authentication events audited.

## API

- [ ] organization-scoped list endpoint works;
- [ ] organization-scoped detail endpoint works;
- [ ] filtering works;
- [ ] pagination works;
- [ ] deterministic ordering works;
- [ ] OpenAPI is accurate;
- [ ] no audit write endpoint is exposed.

## Tests

- [ ] valid event creation;
- [ ] invalid action;
- [ ] invalid actor;
- [ ] suspended actor;
- [ ] cross-tenant target;
- [ ] cross-tenant list;
- [ ] cross-tenant detail;
- [ ] cross-tenant filter;
- [ ] role matrix;
- [ ] immutability;
- [ ] redaction;
- [ ] request ID;
- [ ] pagination;
- [ ] integration events;
- [ ] bulk import events;
- [ ] full regression suite.

## Gate

All must pass:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

and the commands must be run against the canonical PostgreSQL environment.

---

# 38. What must not be done

Do not turn Phase 11 into:

```text
event sourcing
Kafka/RabbitMQ
a distributed event bus
a data warehouse
an analytics platform
an AI anomaly detector
a full compliance certification system
a frontend audit-log redesign
a payment gateway
an accounting system
```

Do not:

```text
add organization IDs to every model merely for auditing
create a second permission system
create audit signals for every model save
log every read request
store full serialized domain objects
store uploaded spreadsheets
store authentication secrets
allow tenant IDs from arbitrary request bodies to define audit ownership
declare the phase complete because the audit endpoint returns 200
```

---

# 39. Expected final architecture

```text
                        ┌───────────────────────────┐
                        │ Existing SaaS mutation    │
                        │ service / API workflow    │
                        └──────────────┬────────────┘
                                       │
                                       │ authorized mutation
                                       ▼
                        ┌───────────────────────────┐
                        │ audit_logs.services       │
                        │ record_event(...)         │
                        └───────┬────────┬──────────┘
                                │        │
                    validation │        │ sanitization
                                │        │
                                ▼        ▼
                     actor/tenant       safe metadata
                     target/action      change summary
                                │        │
                                └────┬───┘
                                     ▼
                         ┌────────────────────────┐
                         │ immutable AuditLog     │
                         │ PostgreSQL             │
                         └───────────┬────────────┘
                                     │
                                     ▼
                         ┌────────────────────────┐
                         │ org-scoped read API    │
                         │ owner/admin only       │
                         └────────────────────────┘
```

---

# 40. Final implementation principle

The implementer must repeatedly ask:

> “Would an academy owner or support engineer learn something important from this event, without exposing confidential data or creating noise?”

If the answer is no, do not audit it.

If the answer is yes, integrate the event at the authoritative business mutation boundary and ensure the event is:

```text
tenant-safe
actor-correct
transactionally honest
privacy-safe
immutable
queryable
testable
documented
```

That is the acceptance standard for SaaS Phase 11.
