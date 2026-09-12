# SaaS Phase 8 — Notification Domain

## Status

READY FOR IMPLEMENTATION

## Objective

Introduce the shared notification/event domain required for the Quran Academy multi-tenant SaaS platform.

The target architecture is:

```text
Domain action
    ↓
Notification event
    ↓
Notification service
    ↓
Provider adapter
    ↓
Delivery record
```

This phase establishes the contract for academy-aware product notifications without coupling core domain models to WhatsApp, Telegram, email, or push providers.

## Repository baseline

Branch: `main`

Baseline commit:
`2127f80cff6512b6ad52fe0f057e1df4cb7fd61a`

Commit:
`feat(saas): implement Phase 7 teacher payout tenancy and complete phase gate`

Completed SaaS phases: 1 through 7, with Phase 7 being Teacher Payout Tenancy.

Existing domain apps include:

```text
accounts/
organizations/
curriculum/
scheduling/
pricing/
assessment/
payouts/
```

There is no completed central notification domain to treat as already sufficient for this phase.

---

# 1. Architecture principles

1. Notifications are a shared platform capability.
2. Source domains own the business action that causes the event.
3. Notification services own event creation, recipient validation, channel intent and delivery history.
4. Provider adapters own provider-specific payloads and SDK calls.
5. Every academy-owned notification has deterministic organization ownership.
6. Recipient visibility is tenant-safe.
7. Delivery attempts are auditable.
8. Notification data is minimal and privacy-safe.
9. Repeated events follow an explicit idempotency strategy.
10. Provider failure never deletes the notification or another channel's delivery record.

---

# 2. Tasks

| Task | Purpose | Status |
|---|---|---|
| 8.1 | Notification architecture and ownership audit | DONE |
| 8.2 | Core notification and delivery models | DONE |
| 8.3 | Event creation/service layer | DONE |
| 8.4 | Provider interfaces and adapters | DONE |
| 8.5 | Tenant-safe notification API | DONE |
| 8.6 | Initial domain event integrations | DONE |
| 8.7 | Privacy, idempotency and isolation tests | DONE |
| 8.8 | PostgreSQL, OpenAPI, acceptance and documentation | DONE |

---

# 3. Task 8.1 — Notification architecture and ownership audit

Before schema changes inspect:

```text
accounts/
organizations/
scheduling/
curriculum/
assessment/
pricing/
payouts/
config/
```

Specifically map:

```text
Booking
PlacementResult
SessionAssessment
ProgressSnapshot
OrganizationMembership
teacher invitation flow
```

For every initial event record:

```text
source model
source primary key
source action
organization ownership path
recipient
minimum payload
candidate channels
event identity/idempotency key
```

Required events:

```text
BOOKING_CONFIRMED
BOOKING_CANCELLED
PLACEMENT_REVIEWED
PROGRESS_READY
TEACHER_INVITATION
```

### Acceptance

Task 8.1 is complete when each event has a deterministic:

```text
source → organization → recipient → event identity
```

flow.

---

# 4. Task 8.2 — Core notification and delivery models

Create the central notification domain using repository conventions.

## Notification

Recommended conceptual fields:

```text
id
organization
event_type
recipient
title/summary
payload
created_at
read_at
```

Do not put provider SDK objects or provider-specific payload structures in this model.

## NotificationDelivery

Recommended conceptual fields:

```text
id
notification
channel
provider
status
attempt_count
provider_message_id
error_code
error_message
attempted_at
delivered_at
created_at
```

Use the smallest safe field set.

## Tenant integrity

Enforce:

- notification belongs to one academy;
- delivery belongs to one notification;
- notification and delivery cannot cross academies;
- recipient is valid for the notification academy at creation;
- payload is JSON-serializable;
- secrets are never stored in notification payloads.

Provide an organization-scoped queryset/helper consistent with the other SaaS apps.

For example:

```python
Notification.objects.in_organization(organization)
NotificationDelivery.objects.in_organization(organization)
```

### Acceptance

Direct ORM/service writes cannot create an invalid notification/delivery tenant relationship.

---

# 5. Task 8.3 — Event creation and service layer

Create a provider-neutral service such as:

```python
create_notification(
    organization=organization,
    event_type=event_type,
    recipient=recipient,
    payload=payload,
)
```

The service must:

1. validate academy ownership;
2. validate recipient access;
3. validate event type;
4. apply the documented event identity/idempotency rule;
5. create the notification;
6. prepare delivery records where appropriate.

Source domains must not construct WhatsApp, Telegram, SMTP, or push payloads.

## Initial event types

Implement:

```text
BOOKING_CONFIRMED
BOOKING_CANCELLED
PLACEMENT_REVIEWED
PROGRESS_READY
TEACHER_INVITATION
```

## Idempotency

Define identity deliberately. A candidate is:

```text
source_type + source_id + event_type + recipient
```

but use event-specific rules where necessary.

Expected example:

```text
booking confirmed + same recipient + same booking
→ no uncontrolled duplicate

same booking later cancelled
→ separate cancellation event is valid
```

### Acceptance

Source domains can request notifications without knowing provider implementation details.

---

# 6. Task 8.4 — Provider interfaces and adapters

Introduce small provider-neutral interfaces, for example:

```text
send_message(...)
send_template(...)
send_email(...)
```

The exact names may follow repository conventions.

## Email

Create an email adapter boundary.

## WhatsApp

Create a WhatsApp adapter boundary.

## Telegram

Create a Telegram adapter boundary.

## Push

Define the interface boundary; a production implementation may remain deferred unless the repository already has one.

Provider selection must be handled by notification configuration/service logic, not embedded in Booking, Assessment, Payout or other domain models.

Production provider credentials are not required to complete the architecture unless already safely configured in the repository.

### Acceptance

Core notification code invokes provider-neutral interfaces. Provider-specific imports and payload translation stay in adapters.

---

# 7. Task 8.5 — Tenant-safe notification API

Follow the established organization-scoped API convention.

Suggested surface:

```text
GET  /api/notifications/organizations/<organization_pk>/mine/
GET  /api/notifications/organizations/<organization_pk>/admin/
GET  /api/notifications/organizations/<organization_pk>/<id>/
POST /api/notifications/organizations/<organization_pk>/<id>/read/
GET  /api/notifications/organizations/<organization_pk>/deliveries/
```

Exact path names may follow repository conventions, but organization context is mandatory.

## Recipient endpoint

A user may see only:

```text
recipient = request.user
AND
organization = selected organization
```

with the appropriate current membership/access policy.

## Admin endpoint

Owner/admin users may inspect academy notification history and delivery outcomes for their own academy only.

## Read endpoint

A recipient can mark only their own notification as read.

## Delivery endpoint

Delivery history is academy-scoped. Provider secrets must never be exposed.

## Permission rules

Use existing:

```text
OrganizationMembership
OrganizationRole
MembershipStatus
active_membership()
```

Do not create a new role system.

### Acceptance

Every notification endpoint has explicit organization context and server-side membership/role checks.

---

# 8. Task 8.6 — Initial domain integrations

Wire these existing business actions into the notification layer.

## Booking confirmed

Create a notification for the correct recipient(s), with only the necessary fields:

```text
academy
booking/session reference
student/parent recipient
teacher
session timing
join link when authorized
```

## Booking cancelled

Create the appropriate cancellation/update notification.

Do not alter existing booking cancellation rules.

## Placement reviewed

Create a notification when a placement result is reviewed and ready for the intended student/parent recipient.

## Progress ready

Create a notification when a relevant progress update becomes available.

Do not expose internal quality-control fields.

## Teacher invitation

Create an invitation notification for the invited teacher in the academy that issued the invitation.

Do not put secrets or raw authentication credentials in the general notification payload.

### Acceptance

All five initial source actions emit correct tenant-scoped events without changing unrelated business behavior.

---

# 9. Task 8.7 — Privacy, idempotency and isolation tests

Add adversarial tests covering:

## Tenant isolation

```text
Academy A admin cannot list Academy B notifications.
Academy A admin cannot inspect Academy B deliveries.
Known notification id from B cannot be read by A.
Known delivery id from B cannot be read by A.
```

## Recipient isolation

```text
Teacher A cannot read Teacher B notifications.
Parent A cannot read Parent B notifications.
Student A cannot read Student B notifications.
```

## Membership

```text
active member      → allowed where role permits
suspended member   → denied for current academy access
non-member         → denied
```

## Event integrity

Verify each event has:

```text
correct academy
correct recipient
correct type
minimum required payload
```

## Cross-tenant attacks

Reject:

```text
Academy A source event + Academy B recipient
Academy A notification + Academy B delivery
Academy A event + Academy B provider configuration
```

## Idempotency

Verify repeated event submission follows the documented identity strategy.

## Provider failure

Simulate a provider failure and verify:

```text
notification remains
failed delivery is recorded
other channel deliveries remain independent
no unrelated academy data changes
```

### Regression requirement

Existing scheduling, routing, pricing, assessment and payout tests must continue to pass.

---

# 10. Task 8.8 — PostgreSQL, OpenAPI, acceptance and documentation

Run against PostgreSQL:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

## Manual acceptance

### Admin

```text
Admin selects Academy A
→ sees Academy A notifications
→ sees Academy A delivery outcomes
→ cannot see Academy B
```

### User

```text
Teacher/Parent/Student selects Academy A
→ sees only their own notifications
→ marks one read
→ cannot access another user's notification
```

### Event flow

```text
booking confirmed
→ notification created

booking cancelled
→ notification created

placement reviewed
→ notification created

progress ready
→ notification created

teacher invited
→ notification created
```

### Provider boundary

Verify core notification services do not directly import provider SDKs.

### OpenAPI

Document, where endpoints exist:

- organization path parameter;
- authentication;
- membership requirement;
- owner/admin permissions;
- recipient self-scope;
- read endpoint;
- delivery visibility;
- error responses.

Update `learnings.md` and `tech-debt.md` only for actual decisions or deliberate deferrals.

---

# 11. Privacy rules

Do not include in ordinary notification payloads:

```text
internal assessment QC notes
lead-only notes
private teacher financial information
unnecessary family pricing data
provider secrets
access tokens
credentials
```

A session join link may be included only when the recipient is authorized for that session.

---

# 12. Explicitly out of scope

- full production WhatsApp rollout;
- full production Telegram rollout;
- full push infrastructure;
- marketing/campaign automation;
- complex preference center;
- background job platform;
- advanced retry/backoff infrastructure;
- notification analytics warehouse;
- video provider abstraction;
- payment gateway work;
- frontend notification-center redesign.

---

# 13. Definition of done

SaaS Phase 8 is complete only when:

1. A central notification domain exists.
2. Academy ownership is deterministic.
3. Notification and delivery records are tenant-safe.
4. Recipient visibility is tenant-safe.
5. Provider adapters are isolated from domain models.
6. The five initial event types are implemented.
7. Event identity/idempotency is defined and tested.
8. Provider failures are auditable.
9. Cross-tenant notification tests pass.
10. Recipient privacy tests pass.
11. Existing scheduling/routing/pricing/assessment/payout tests remain green.
12. PostgreSQL migrations/checks pass.
13. OpenAPI is updated where notification endpoints exist.
14. Documentation decisions are recorded.
15. A coherent git commit marks the phase complete.

---

# 14. Stop and ask instead of guessing

Stop before implementation when:

- notification ownership is ambiguous;
- recipient/academy relationship is unclear;
- event idempotency semantics conflict with legitimate repeated events;
- provider credential handling requires a new security architecture;
- a requested feature becomes marketing automation;
- background infrastructure becomes a prerequisite outside this phase;
- a notification would expose internal/private domain data.

Routine model, queryset, service, permission, adapter, endpoint, migration and test work may proceed when the rule is explicit above.
