# Quran Academy SaaS Development Rules

## Repository baseline

Repository: `adewoye-saheed-dML/quran_acad`
Target branch: `main`

Current completed SaaS phase: **SaaS Phase 10 — Bulk Import**

Current audited commit:
`bbcfa7cbf9448c7e895266ee994ade436c5ad013`

Commit message:
`feat(saas): implement Phase 9 video provider abstraction and complete phase gate`

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

The next implementation phase is **SaaS Phase 11**.

## Accepted SaaS Phase 10 decisions

- Used `openpyxl` for XLSX support, avoiding heavy data-science dependencies (like `pandas`).
- An all-or-nothing transactional boundary is applied during commit.
- User `username` falls back to `email` when created from an import.
- Missing `timezone` falls back to `UTC`.
- `OrganizationRole` assigns `TEACHER` for teachers and `STAFF` for parents/students (as opposed to full member role definitions, this fits the current constraints).
- Did not build arbitrary model importers or frontend spreadsheet editors in this phase.
- Handled tenant isolation carefully with transaction-safe commit.

## Accepted SaaS Phase 9 decisions

- Video Provider Abstraction is complete.
- `Booking` objects no longer derive video details from hardcoded Jitsi logic. Instead, `scheduling/providers.py` handles the provider abstraction, making the DB the boundary of truth.
- `video_room_name` has been replaced by `video_provider`, `video_provider_meeting_id`, and `video_join_url`.
- Meeting creation leverages idempotency based on `organization_id` and `booking_id`.
- Payout validation prevents cross-academy booking, teacher and cohort combinations.
- Historical payout records remain immutable.
- Payout generation is organization-scoped and repeat-safe.
- Payout APIs use explicit organization-scoped routes.
- Legacy unscoped payout routes are retired.
- Owner/admin payout operations require active organization membership and suitable organization role.
- Teacher self-service is limited to the teacher's own records in the selected academy.
- Student and parent payout access is denied.
- Adversarial payout tenant-isolation tests are complete.
- SaaS Phase 7 passed its phase gate and was committed at the baseline above.

## SaaS Phase 8 objective

Build a central notification/event domain that separates:

```text
domain action
    ↓
internal notification event
    ↓
delivery service
    ↓
provider adapter
    ↓
delivery attempt/result
```

The notification system is the shared communication boundary for future:
- WhatsApp
- Telegram
- Email
- Push

Do not let Booking, Assessment, Payouts, Scheduling, or other domain models contain provider-specific communication logic.

## Core rules

1. Every academy-owned notification has deterministic academy ownership.
2. A notification recipient must be valid for the academy context.
3. Delivery records belong to the same academy as their notification.
4. Cross-academy recipient/provider combinations are rejected.
5. Owner/admin history is academy-scoped.
6. Ordinary users see only notifications intended for them in academies where they have valid access.
7. Provider failures never weaken tenant isolation.
8. Provider callbacks cannot switch tenant context using a client-supplied academy id.
9. Platform-level/system events must be distinct from academy-owned events.
10. Tenant filtering happens in querysets/services, not only serializers.

## Initial event types

Implement the roadmap's first required events:

- `BOOKING_CONFIRMED`
- `BOOKING_CANCELLED`
- `PLACEMENT_REVIEWED`
- `PROGRESS_READY`
- `TEACHER_INVITATION`

Payment/price notifications remain future work unless an existing payment event already exists.

## Provider boundary

Use provider-neutral interfaces such as:

```text
send_message(...)
send_template(...)
send_email(...)
```

Provider-specific SDKs/payloads belong only inside adapters. The core notification models must not contain provider SDK objects, WhatsApp payload structures, Telegram-specific fields, or email MIME structures.

Production credentials for external providers are not required merely to complete this phase unless the repository already has a safe integration boundary.

## Event and delivery model

A notification should conceptually contain:

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

A delivery should conceptually contain:

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

Use repository conventions and the smallest safe field set.

## Idempotency

Define event identity explicitly. A useful candidate is:

```text
source_type + source_id + event_type + recipient
```

but do not impose one universal uniqueness rule when legitimate repeated events are possible. Document per-event identity where necessary.

A repeated booking confirmation must not create uncontrolled duplicate notifications; a later cancellation is a different event and may legitimately create a new notification.

## Domain integration rules

Source domains may emit notification intents/events, but must not call:

- WhatsApp SDKs
- Telegram SDKs
- SMTP/provider SDKs
- push SDKs

directly.

Academy ownership should be derived from the authoritative source domain relationship. Do not add redundant organization columns merely for notifications.

## Permissions

Owner/admin:
- academy notification history
- delivery outcomes
- notification configuration where implemented

Teacher:
- own notifications only
- no other teacher's records

Parent:
- own notifications intended for them/linked children as explicitly modeled
- no other academy's notification history

Student:
- own notifications only

Suspended/non-members:
- no current academy notification access

## API direction

Prefer the established organization-scoped convention:

```text
GET  /api/notifications/organizations/<organization_pk>/mine/
GET  /api/notifications/organizations/<organization_pk>/admin/
GET  /api/notifications/organizations/<organization_pk>/<id>/
POST /api/notifications/organizations/<organization_pk>/<id>/read/
GET  /api/notifications/organizations/<organization_pk>/deliveries/
```

Exact names may follow repository conventions. Internal event creation should normally be service-driven, not dependent on a public ingestion endpoint.

## Webhook boundary

Provider callbacks must:
1. identify the provider;
2. verify/authenticate the callback when supported;
3. resolve the delivery safely;
4. update only the matched delivery;
5. never trust a callback-supplied organization id as tenant authority;
6. never expose another academy's data.

## Privacy

Notification payloads must not contain:
- internal assessment QC fields;
- lead-only notes;
- private teacher financial data;
- unnecessary family pricing data;
- provider secrets;
- access tokens or authentication credentials.

A join link is permitted only when the recipient is authorized for the session.

## Scope boundary

Do not turn Phase 8 into:
- full production WhatsApp/Telegram rollout;
- marketing automation;
- campaign management;
- full push infrastructure;
- large preference center;
- queue/worker platform;
- retry/backoff infrastructure;
- video provider abstraction;
- payment gateway work;
- frontend notification-center redesign.

The phase establishes the stable notification/event contract and tenant-safe delivery boundary. Background jobs and advanced retries can follow later.

## Testing and phase gate

At minimum run against PostgreSQL:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Required tests include:
- Academy A cannot read Academy B notifications/deliveries.
- Known ids cannot cross tenant boundaries.
- Users cannot read another user's notifications.
- Suspended/non-members are denied.
- Source event, organization and recipient relationships are correct.
- Invalid cross-tenant recipient/delivery relations are rejected.
- Event identity/idempotency behaves as documented.
- Provider failure is recorded without deleting the notification or affecting another delivery.
- Existing scheduling, routing, pricing, assessment and payout tests remain green.

## Documentation

Update where decisions actually change:
- `learnings.md`
- `tech-debt.md`
- SaaS Phase 8 spec status/tasks
- OpenAPI documentation for exposed notification endpoints

## Working discipline

- Audit before schema changes.
- Implement one numbered task at a time.
- Reuse organization and membership helpers.
- Keep provider code at the infrastructure boundary.
- Preserve current business rules.
- Run focused tests during development.
- Run the full PostgreSQL gate at the end.
- Commit each coherent implementation unit.
- Do not mark the phase complete from documentation alone.

## Stop instead of guessing

Stop before implementation when:
- notification ownership is ambiguous;
- recipient/academy relationship is unclear;
- event idempotency conflicts with legitimate repeated events;
- provider credential handling requires a new security architecture;
- a requested feature becomes marketing automation;
- background infrastructure becomes a prerequisite beyond this phase;
- a notification would expose internal/private domain data.

After SaaS Phase 8 is accepted, stop for the next explicitly approved phase.
