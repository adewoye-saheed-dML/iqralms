# Quran Academy SaaS — Phase 9 Claude Instructions

## Repository

Repository: `adewoye-saheed-dML/quran_acad`

Target branch: `main`

## Current phase

Implement **SaaS Phase 9 — Video Provider Abstraction**.

The Phase 9 specification is:

`specs/saas/SaaS Phase 9 — Video Provider Abstraction.md`

Read that specification before making changes. Treat it as the source of truth for scope, architecture, tests, and the phase gate.

---

## Current baseline

SaaS Phases 1–8 are already complete.

Do not redesign completed tenancy, curriculum, scheduling, pricing, assessment, payout, or notification decisions unless the existing implementation creates a direct contradiction with the Phase 9 specification.

The goal is to isolate video-provider logic, not to rewrite scheduling.

---

## Primary objective

Create a provider-neutral meeting abstraction.

Target:

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

Jitsi is the first implementation.

Do not implement Google Meet, Teams, Zoom, OAuth, conferencing analytics, recordings, or frontend meeting UI.

---

## Non-negotiable rules

1. Scheduling/domain code must not contain provider-specific Jitsi logic after the refactor.
2. Jitsi-specific URL/room construction belongs inside the Jitsi adapter.
3. The provider interface must be generic enough for another provider later.
4. Meeting authorization remains an application-domain responsibility, not a provider responsibility.
5. Academy tenancy must remain enforced server-side.
6. Do not trust a client-supplied organization id without checking membership.
7. Do not use a provider URL or room name as the authorization mechanism.
8. Do not add redundant organization fields when ownership is already derivable safely from an authoritative academic relationship.
9. Meeting creation must be repeat-safe for the same academic session.
10. Phase 8 notification code must remain provider-neutral.
11. Preserve all existing scheduling/routing/capacity/time-zone behaviour.
12. Do not add frontend work.

---

## Required implementation sequence

### Task 9.1 — Audit

Inspect the repository before modifying models.

Find:

- Jitsi room/link generation;
- Booking/Cohort/session relationships;
- existing meeting storage;
- organization ownership;
- academy settings;
- notification integration;
- meeting access endpoints;
- current tests.

Write down the authoritative ownership path before introducing a new field.

### Task 9.2 — Provider contract

Introduce a small provider-neutral interface/service supporting at least:

```python
create_meeting(...)
get_meeting(...)
```

Add cancellation only if existing application semantics genuinely need it.

Do not expose Jitsi-specific fields in the interface.

### Task 9.3 — Meeting persistence

Reuse an existing meeting/session object when possible.

Persist provider-neutral information such as:

```text
provider
provider_meeting_id
join_url
created_at
updated_at
```

Avoid redundant tenant columns.

### Task 9.4 — Jitsi adapter

Move Jitsi-specific room-name and URL generation behind the adapter.

The adapter must not:

- enforce academy permissions;
- change booking state;
- create notifications;
- inspect unrelated academies.

### Task 9.5 — Academy provider configuration

Add only the minimum organization-scoped configuration needed to select the video provider.

Default existing academies safely to Jitsi.

Reject unsupported provider names on the server.

Do not build provider-management UI.

### Task 9.6 — Scheduling integration

Replace direct Jitsi usage in scheduling with the meeting service.

Do not change:

- routing;
- capacity;
- availability;
- booking state transitions;
- cancellation rules;
- timezone behaviour.

### Task 9.7 — Notification integration

Phase 8 notifications may request an authorized join URL through the meeting service.

Do not add Jitsi-specific fields or imports to the notification domain.

### Task 9.8 — Security and tests

Add tests for:

- provider contract;
- Jitsi adapter;
- repeat-safe meeting creation;
- cross-academy access;
- known-ID tenant attacks;
- cross-academy creation attempts;
- provider-config isolation;
- teacher/parent/student access;
- suspended membership;
- non-members;
- notification integration;
- existing regressions.

### Task 9.9 — Gate

Run against PostgreSQL:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Verify OpenAPI if public meeting behaviour changed.

Perform a manual two-academy acceptance test.

### Task 9.10 — Documentation

Update only the documentation affected by real implementation decisions:

- `learnings.md`
- `tech-debt.md`
- `CLAUDE.md`
- Phase 9 specification
- OpenAPI documentation

Do not claim unsupported providers are implemented.

---

## Code quality expectations

Prefer:

- small service boundaries;
- explicit types where repository conventions support them;
- existing organization/membership helpers;
- existing queryset/permission patterns;
- model relationships as ownership sources;
- focused tests close to the domain being changed.

Avoid:

- duplicate organization logic;
- hidden global state;
- provider-specific conditionals scattered across scheduling;
- serializer-only tenant filtering;
- "temporary" shortcuts that weaken authorization;
- broad rewrites unrelated to the phase.

---

## Stop instead of guessing

Stop and inspect further if:

- meeting ownership cannot be determined safely;
- current Jitsi creation has multiple conflicting entry points;
- the existing meeting model is used as more than a provider boundary;
- changing the meeting abstraction would alter booking semantics;
- a new security mechanism is needed for provider credentials;
- the requested feature is outside the Phase 9 scope.

Do not solve ambiguity by inventing a new architecture without checking the existing repository relationships first.

---

## Phase completion standard

Do not mark Phase 9 complete merely because the code compiles.

It is complete only when:

- provider abstraction exists;
- Jitsi is behind the adapter;
- meeting creation/retrieval uses the abstraction;
- tenancy and authorization are tested;
- duplicate meeting creation is controlled;
- notifications remain provider-neutral;
- PostgreSQL checks pass;
- regression tests pass;
- API documentation is updated where required;
- documentation is updated;
- a coherent phase-completion commit is created.
