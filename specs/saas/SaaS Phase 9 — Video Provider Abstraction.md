# SaaS Phase 9 — Video Provider Abstraction

## Status

READY FOR IMPLEMENTATION

## Repository

`adewoye-saheed-dML/quran_acad`

## Target branch

`main`

## Baseline

The repository has completed SaaS Phases 1–8:

1. Organization Foundation
2. Accounts Tenancy
3. Curriculum Tenancy
4. Scheduling Tenancy
5. Pricing Tenancy
6. Assessment Tenancy
7. Teacher Payout Tenancy
8. Notification Domain

Phase 9 must build on the existing tenant-aware scheduling and notification architecture. Do not redesign earlier phase decisions unless the current implementation makes the Phase 9 contract impossible to enforce safely.

---

# 1. Objective

Introduce a provider-neutral video/meeting abstraction so that scheduling and booking logic are no longer structurally tied to Jitsi.

Jitsi remains the first and only production-style provider implementation in this phase.

The target architecture is:

```text
Booking / Session
        ↓
Meeting service
        ↓
MeetingProvider interface
        ↓
Jitsi adapter
        ↓
Meeting record / provider result
```

The application must be able to add another provider later, such as Google Meet, without rewriting booking validation, routing, session access, or notification logic.

---

# 2. Why this phase exists

The current platform already generates Jitsi meeting links as part of the scheduling layer.

The roadmap requires video integration to become provider-neutral before frontend development:

- Jitsi should become one implementation rather than the permanent scheduling dependency.
- Academy configuration should eventually determine the preferred provider.
- Booking and scheduling code should not construct provider-specific URLs directly.
- Notification delivery should consume an authorized meeting/join-link result rather than know how a provider creates rooms.

Do not turn this phase into a Google Meet integration, OAuth product, production conferencing system, or frontend meeting UI.

---

# 3. Scope

## In scope

- Audit current Jitsi creation and retrieval logic.
- Define a provider-neutral meeting interface/service.
- Create a meeting/session persistence boundary if the current model does not already provide one.
- Move Jitsi-specific logic behind an adapter.
- Store provider identity and provider meeting identifier separately from the public join URL.
- Make meeting creation idempotent for the same academic session/booking context.
- Make meeting lookup/retrieval provider-neutral.
- Update scheduling/session access code to use the abstraction.
- Update notification integrations to obtain meeting links through the abstraction.
- Add tenant-safe provider configuration at academy level where required by the existing organization architecture.
- Add adversarial cross-academy tests.
- Add provider-boundary tests proving scheduling does not import provider-specific implementation details.
- Update OpenAPI where public meeting-related API behaviour changes.
- Update documentation only for decisions actually introduced in this phase.

## Explicitly out of scope

- Google Meet implementation.
- Microsoft Teams implementation.
- Zoom implementation.
- Provider OAuth authorization flows.
- Production webhook/reconciliation infrastructure for every provider.
- Recording, transcription, attendance ingestion, or conferencing analytics.
- Frontend meeting-room UI.
- Mobile apps.
- Background job/retry infrastructure.
- Payment or subscription work.
- Notification preference-center work.

---

# 4. Current architecture audit

Before changing models or services, inspect:

```text
accounts/
organizations/
scheduling/
notifications/
config/
```

At minimum locate:

```text
Booking
Cohort
session/meeting fields, if already present
Jitsi URL generation
Jitsi room-name generation
notification event integrations
organization settings
organization membership helpers
```

Document:

```text
current source object
current Jitsi creation point
current room identifier
current join URL storage/retrieval
current authorization path
current organization ownership path
current notification dependency
```

Do not duplicate an existing authoritative relationship.

---

# 5. Core architecture rules

## 5.1 Provider-neutral core

Core scheduling/domain code must not directly depend on Jitsi SDKs, Jitsi-specific payload structures, or hard-coded Jitsi URL construction.

The scheduling layer should know only that it can ask a provider-neutral service for a meeting.

A conceptual interface is:

```python
class MeetingProvider:
    def create_meeting(...): ...
    def get_meeting(...): ...
```

Exact naming may follow repository conventions.

The interface must describe product behaviour, not Jitsi implementation details.

---

# 6. Meeting provider contract

The provider boundary should support at least:

```text
create_meeting
get_meeting
```

Where needed, the abstraction may also support:

```text
cancel_meeting
```

but cancellation must only be included if the existing product semantics require it.

A provider result should conceptually contain:

```text
provider
provider_meeting_id
join_url
display_name/label where needed
```

Do not expose raw provider SDK objects to scheduling/domain code.

The provider interface must not require Jitsi-specific fields such as:

```text
room_name
Jitsi URL fragments
Jitsi-specific configuration objects
```

outside the Jitsi adapter.

---

# 7. Jitsi adapter

Keep the existing Jitsi behaviour as the first adapter.

The adapter is responsible for:

- constructing the Jitsi room identifier;
- generating the join URL;
- translating repository meeting requests into Jitsi details;
- returning provider-neutral meeting information.

The adapter must not:

- enforce academy permissions;
- decide whether a user may join a class;
- query another academy's records;
- alter booking state;
- create notifications directly.

Those responsibilities remain outside the provider adapter.

---

# 8. Meeting persistence

If the repository already stores a stable meeting/session object, extend it rather than creating a duplicate object.

The persisted meeting concept should support:

```text
provider
provider_meeting_id
join_url
created_at
updated_at
```

Only add additional fields when the existing implementation requires them.

## Ownership

Meeting ownership must be derived from the authoritative academic relationship wherever possible.

Do not add a redundant organization foreign key solely for convenience when organization ownership can already be derived safely from the booking/cohort/session relationship.

If a direct organization field is genuinely required by the current model, document why before adding it.

## Provider identity

Persist the provider identity explicitly.

Example:

```text
jitsi
```

Do not infer the provider by parsing the URL.

---

# 9. Academy provider configuration

The SaaS roadmap anticipates academy-level configuration such as:

```text
preferred video provider
```

Phase 9 should establish the configuration boundary required to support this safely.

For this phase:

- Jitsi may be the default provider.
- Existing academies must continue to work without manual reconfiguration.
- Unsupported providers must be rejected clearly.
- A client must not be able to select an arbitrary provider string and bypass server-side policy.
- Provider selection must be academy-scoped.

Do not build a full provider marketplace or admin UI.

---

# 10. Meeting creation lifecycle

The desired flow is:

```text
booking/session becomes eligible for a meeting
        ↓
meeting service resolves academy provider
        ↓
provider adapter creates meeting
        ↓
meeting record is stored
        ↓
booking/session references meeting
        ↓
notification layer may use authorized join URL
```

Meeting creation must be safe to call repeatedly.

For the same academic session, repeated creation requests must not create uncontrolled duplicate meetings.

A suitable identity should be based on the authoritative academic session/booking object, for example:

```text
organization + booking/session id
```

Do not use a client-generated room name as the sole idempotency boundary.

---

# 11. Booking and scheduling integration

Update the current scheduling flow so that:

- booking validation remains unchanged;
- routing logic remains unchanged;
- cohort/capacity rules remain unchanged;
- time-zone handling remains unchanged;
- only meeting creation/retrieval moves behind the provider abstraction.

Do not move scheduling business rules into the video adapter.

Do not change the meaning of a confirmed, cancelled, or completed booking merely because the meeting provider is now abstracted.

---

# 12. Authorization and join links

A meeting join URL is sensitive academic access information.

A caller may receive a join URL only when they already have authorization to access the associated session/booking.

Server-side checks must remain in the application domain/API layer.

Do not rely on:

```text
provider_meeting_id
join_url
room name
```

as authorization.

The provider boundary creates or retrieves the meeting. It does not decide who may enter.

---

# 13. Tenant isolation

The implementation must preserve the existing SaaS tenant rules.

Required invariants:

1. Academy A cannot retrieve Academy B's meeting by known object ID.
2. Academy A cannot create a meeting using Academy B's booking/session.
3. A meeting cannot be attached to a booking/session from another academy.
4. Provider configuration from Academy A cannot be used for Academy B.
5. A teacher can access only authorized meetings for academies where they have valid membership.
6. A parent can access only meetings for valid linked/enrolled children.
7. A student can access only meetings for sessions they are authorized to attend.
8. Suspended/non-members cannot use academy meeting endpoints.
9. Notification events cannot expose another academy's join URL.
10. Provider callbacks, if introduced later, must not become a tenant bypass.

Tenant filtering must happen in querysets/services and permission checks, not only serializers.

---

# 14. Notification integration

The Phase 8 notification system must remain provider-neutral.

Notifications may request an authorized meeting/join link through the meeting service.

Do not add Jitsi-specific fields to:

```text
Notification
NotificationDelivery
Booking
Assessment
Payout
```

A notification payload should receive only the minimum authorized meeting information needed by that notification.

For example:

```text
session time
teacher
join URL
```

Do not expose provider credentials or internal provider data.

---

# 15. Public API direction

Follow the established organization-scoped API convention.

If meeting endpoints are needed, prefer a shape consistent with:

```text
GET /api/.../organizations/<organization_pk>/...
```

Do not create an unscoped meeting endpoint that can bypass academy membership.

Potential operations include:

```text
GET meeting for an authorized booking/session
POST meeting creation where the product actually requires explicit creation
```

Exact routes must follow existing repository routing conventions.

The API must:

- require authentication;
- require active organization membership;
- verify that the requested academic object belongs to the selected academy;
- apply role/access rules;
- return consistent forbidden/not-found behaviour according to existing repository conventions.

---

# 16. Error handling

Provider failures must not silently corrupt booking state.

A provider error should be represented explicitly.

Examples:

```text
provider unavailable
meeting creation rejected
unsupported provider
meeting lookup failed
invalid provider configuration
```

Do not swallow provider exceptions and return a fake join URL.

Do not mark a meeting as created unless the provider operation succeeded sufficiently for the repository's persistence contract.

If persistence succeeds but a later provider step fails, preserve the record state and expose the failure deterministically.

---

# 17. Testing requirements

## 17.1 Provider contract tests

Prove that:

- Jitsi implements the provider contract.
- Core scheduling code can call the provider interface without importing Jitsi implementation details.
- Provider result data is normalized.
- Unsupported provider selection fails cleanly.

## 17.2 Idempotency

Prove:

```text
same booking/session + repeated creation
→ one logical meeting
```

and:

```text
different booking/session
→ different meeting identity
```

## 17.3 Tenant isolation

At minimum:

```text
Academy A cannot retrieve Academy B meeting.
Academy A cannot create a meeting for Academy B booking.
Academy A cannot use Academy B provider configuration.
Known meeting id from B cannot be read by A.
```

## 17.4 Recipient/access isolation

Prove:

```text
Teacher A cannot receive unauthorized Teacher B meeting access.
Parent A cannot receive another parent's child's meeting.
Student A cannot receive Student B meeting access.
Suspended member cannot retrieve meeting.
Non-member cannot retrieve meeting.
```

## 17.5 Notification regression

Prove that authorized notification generation can obtain the correct meeting URL without importing Jitsi-specific implementation code.

## 17.6 Existing regressions

All existing scheduling, routing, pricing, assessment, payout, and notification tests must remain green.

---

# 18. Required PostgreSQL phase gate

Run against PostgreSQL:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Also verify:

```text
OpenAPI output where public meeting behaviour changed
```

and perform a manual two-academy acceptance test.

---

# 19. Manual acceptance

## Academy A

```text
Create/confirm a valid session
→ meeting is created using academy video configuration
→ Jitsi is used as the default provider
→ meeting data is stored
→ authorized teacher/student/parent can obtain the join link
```

## Academy B

```text
Create a different session
→ meeting is independent from Academy A
→ Academy A users cannot retrieve it
→ Academy B configuration does not affect Academy A
```

## Repeat creation

```text
Request meeting creation twice for the same session
→ no uncontrolled duplicate logical meeting
```

## Provider boundary

```text
Inspect core scheduling code
→ no direct Jitsi URL construction outside the adapter/provider boundary
```

---

# 20. Documentation

Update only the documents affected by actual implementation decisions:

- `learnings.md`
- `tech-debt.md`
- `CLAUDE.md`
- `specs/saas/SaaS Phase 9 — Video Provider Abstraction.md`
- OpenAPI/API documentation where public routes change

Record deliberate deferrals such as:

- Google Meet
- Teams
- Zoom
- provider OAuth
- advanced conferencing features

Do not claim those providers are supported.

---

# 21. Recommended task breakdown

## Task 9.1 — Architecture audit

Inspect current Jitsi/session implementation and record the ownership and dependency graph.

## Task 9.2 — Provider contract

Introduce the provider-neutral interface/service.

## Task 9.3 — Meeting persistence boundary

Refactor or extend the existing meeting/session storage to persist provider-neutral fields.

## Task 9.4 — Jitsi adapter

Move Jitsi-specific room/link creation behind the adapter.

## Task 9.5 — Academy provider configuration

Introduce the minimum academy-scoped configuration required for provider selection, defaulting safely to Jitsi.

## Task 9.6 — Scheduling integration

Update booking/session meeting creation and retrieval to use the abstraction.

## Task 9.7 — Notification integration

Ensure Phase 8 notifications obtain meeting information through the abstraction and remain provider-neutral.

## Task 9.8 — Security and tenant tests

Add adversarial cross-tenant, membership, authorization, and idempotency tests.

## Task 9.9 — PostgreSQL and API verification

Run migrations, test suite, checks, and OpenAPI verification.

## Task 9.10 — Documentation and phase gate

Update documentation, complete manual acceptance, and create one coherent phase-completion commit.

---

# 22. Definition of done

SaaS Phase 9 is complete only when:

1. A provider-neutral meeting abstraction exists.
2. Jitsi is implemented behind that abstraction.
3. Scheduling no longer constructs Jitsi-specific meeting data directly outside the adapter boundary.
4. Meeting/provider identity is persisted in a provider-neutral form.
5. Academy provider configuration is tenant-safe.
6. Meeting creation is repeat-safe for the same academic session.
7. Authorized users can still obtain the correct meeting link.
8. Unauthorized users cannot cross academy or user boundaries.
9. Notification integration remains provider-neutral.
10. Provider failures are represented explicitly.
11. Existing scheduling/routing/pricing/assessment/payout/notification tests remain green.
12. PostgreSQL migration/check/test gates pass.
13. OpenAPI reflects any public API changes.
14. Documentation records the implementation and deliberate deferrals.
15. A coherent git commit marks SaaS Phase 9 complete.

---

# 23. Stop instead of guessing

Stop before implementation when:

- the current academic session/meeting ownership path is ambiguous;
- existing Jitsi behaviour is relied upon by multiple domains in a way not covered by tests;
- a direct organization field would duplicate a more authoritative ownership relationship without a clear reason;
- provider selection can affect authorization semantics;
- a provider failure would require changing booking business rules;
- provider credentials require a new security architecture;
- the requested work becomes a full external conferencing integration;
- the requested work becomes frontend implementation.

Routine model, service, adapter, queryset, permission, route, migration, and test work may proceed when the rule is explicit above.

---

# 24. Phase boundary

Phase 9 establishes the stable video-provider boundary.

After Phase 9, the next likely SaaS work remains:

- bulk CSV/XLSX import;
- audit logging;
- API contract freeze;
- frontend readiness.

Do not pull those features into Phase 9 unless a concrete dependency is discovered and documented.
