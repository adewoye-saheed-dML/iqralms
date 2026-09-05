# Quran Academy Platform

Django REST backend for a Quran/Arabic teaching academy evolving into a **multi-tenant SaaS platform for Quran academies, Islamic schools, and related learning institutions**.

The overall product intent is in `docs/quran-academy-mvp-spec.md`.

Detailed requirements for each development stage live in `specs/`.

The original single-academy product phases established the existing domain behaviour. The new SaaS work must extend that system without casually redesigning or rewriting the existing business logic.

## Phase status

### Original product phases

- Phase 1 Accounts — DONE
- Phase 2 Curriculum & placement — DONE
- Phase 3 Scheduling — DONE
- Phase 3.5 Booking debt cleanup — DONE
- Phase 4 Routing — DONE
- Phase 5 Pricing & preferred-teacher waitlist — DONE
- Phase 6 Production hardening — DONE
- Phase 7 Assessment & progress — IMPLEMENTED; manual acceptance pending
- Phase 8 Payouts & statements — IMPLEMENTED; manual acceptance pending

### SaaS expansion

- SaaS Phase 1 Organization foundation — IMPLEMENTED; manual acceptance pending

A phase is not complete merely because code exists. Do not start the next phase until the current phase's acceptance criteria are verified and the completed work is committed.

## Product direction

The product is no longer treated as a system for one Quran academy only.

The target product is:

> A multi-tenant operating platform for Quran and Islamic learning institutions.

The platform should eventually allow many independent academies to operate on the same application while keeping their users, curriculum, schedules, pricing, assessment, financial records, and administration isolated from one another.

Examples of target institutions:

- Quran academies
- Hifz schools
- Tajweed schools
- Arabic learning institutions
- Islamic learning centres
- Online Quran teaching businesses
- Small and medium Islamic education organizations

The platform may eventually integrate with communication and video tools such as WhatsApp, Telegram, email, Jitsi, and Google Meet.

Those integrations are **not** part of the first SaaS foundation.

## SaaS architecture principle

The fundamental boundary is:

```text
Organization
    |
    +-- Members
    +-- Curriculum
    +-- Students
    +-- Teachers
    +-- Scheduling
    +-- Pricing
    +-- Assessment
    +-- Payouts
    +-- Future integrations
```

An `Organization` represents one independent academy/institution operating on the platform.

A user may eventually belong to more than one organization.

Therefore:

```text
User
    =
identity

Organization Membership
    =
user's relationship with an academy
```

Do not confuse the global identity of a `User` with the user's role inside an individual organization.

## Critical SaaS rule

The existing:

```text
User.role
```

must **not** be replaced or reinterpreted casually during the SaaS migration.

The current values:

```text
lead
sub
student
parent
```

already drive existing business logic and permissions.

Organization-level roles are a separate concept:

```text
owner
admin
staff
teacher
```

The initial SaaS phases introduce organization membership alongside the existing account role system.

A later, explicitly scoped migration may decide whether the old role model should eventually be simplified.

Do not perform that migration during SaaS Phase 1.

## Multi-tenancy migration principle

The migration from the current single-academy backend to multi-tenancy must happen incrementally.

Do not add an `organization` field to every model in one large refactor.

Preferred sequence:

```text
SaaS Phase 1
Organization foundation
        |
        v
SaaS Phase 2
Organization memberships and permissions
        |
        v
SaaS Phase 3
Accounts tenancy
        |
        v
SaaS Phase 4
Curriculum tenancy
        |
        v
SaaS Phase 5
Scheduling tenancy
        |
        v
SaaS Phase 6
Pricing tenancy
        |
        v
SaaS Phase 7
Assessment tenancy
        |
        v
SaaS Phase 8
Payout tenancy
        |
        v
SaaS Phase 9
Academy onboarding
        |
        v
SaaS Phase 10
Academy settings
        |
        v
SaaS Phase 11
Tenant security audit
        |
        v
Frontend
```

Each phase must preserve the behaviour of the completed original phases.

## Token-efficient development method

This repository is intentionally developed in small, inspectable increments. AI assistance must stay scoped to the smallest useful unit.

### Before changing code

1. Read the active phase specification.
2. Read the relevant existing domain files.
3. Choose one acceptance criterion or one clearly bounded implementation task.
4. Check `git status`.
5. Inspect only the files needed for that task.
6. Prefer the current diff and existing repository patterns over rereading finished code.
7. Do not inspect the whole repository unless the task genuinely crosses module boundaries.

### During implementation

- Make one focused change at a time.
- Do not refactor unrelated code.
- Do not add speculative future fields, endpoints, abstractions, or infrastructure.
- Reuse existing repository conventions.
- Preserve behaviour outside the current task.
- Keep product decisions out of implementation guesses.
- When a requirement is ambiguous, stop and ask instead of inventing behaviour.
- Never silently alter a previous-phase invariant to make the new phase easier.

## One task per AI session

A normal coding request should look like:

> Implement SaaS Phase 1 task 1.1 only. Read the SaaS Phase 1 specification and inspect only the files required for that task. Do not modify accounts, curriculum, scheduling, pricing, assessment, or payouts unless the task explicitly requires it. Add focused tests and report the changed files and commands to verify them.

Do not ask AI to implement an entire large SaaS migration in one response.

## SaaS Phase 1 boundary

SaaS Phase 1 introduces only the new organization foundation.

It owns:

- `organizations` Django app;
- `Organization`;
- `OrganizationMembership`;
- organization-level roles;
- organization creation;
- owner membership creation;
- organization membership API;
- tenant membership permissions;
- organization-level security tests.

It does **not** yet own:

- curriculum tenancy;
- scheduling tenancy;
- routing tenancy;
- pricing tenancy;
- assessment tenancy;
- payout tenancy;
- academy branches;
- organization billing;
- WhatsApp;
- Telegram;
- Google Meet;
- Jitsi provider abstraction;
- notifications;
- frontend;
- invitation email delivery;
- user-role migration.

## Organization model principle

The `Organization` model represents the tenant itself.

Recommended fields:

```text
id
name
slug
timezone
is_active
created_at
updated_at
```

The slug identifies the organization in URLs and APIs where appropriate.

Slug uniqueness is global at this stage.

Do not assume organization names are globally unique.

## Organization membership principle

Membership represents a user's relationship with a tenant.

Recommended fields:

```text
id
organization
user
role
status
created_at
updated_at
```

Recommended organization roles:

```text
owner
admin
staff
teacher
```

The exact role names and permissions are defined in the active SaaS phase specification.

A user may belong to multiple organizations.

Do not use `User.role` as a replacement for organization membership.

## Organization ownership principle

Every organization created through the API must have exactly one initial owner.

Creation should be atomic:

```text
create organization
        |
        +-- create owner membership
```

If either operation fails, neither should remain committed.

The owner is represented through membership, not through a separate `Organization.owner` field unless a later design decision explicitly introduces such a field.

Do not create two competing sources of truth for ownership.

## Membership security

Organization membership is the first tenant boundary.

A user must not be able to:

- read another user's organization memberships merely by knowing a user ID;
- read membership records for an organization they do not belong to;
- add themselves as owner;
- create an administrator membership without sufficient organization authority;
- modify another organization's membership;
- access an organization through a relationship belonging to a different organization.

For Phase 1, existing domain models remain untouched, so this phase proves the security boundary independently before other domains become tenant-scoped.

## Existing domain preservation

Do not change the existing business invariants merely to introduce organizations.

### Scheduling

`Booking.clean()` remains the source of booking eligibility.

New booking writers must use the normal `Booking.save()` path.

`TeacherBookingLock` remains part of booking-creation race protection.

Do not weaken or remove it during SaaS Phase 1.

### Pricing

`pricing.PricingAgreement.active_for(student, level)` remains the canonical active agreement lookup.

Family pricing remains separate from teacher payout.

### Waitlist

`TeacherWaitlist` exists only for refused preferred-teacher requests.

Promotion must continue through normal booking validation.

### Placement audio

Placement recordings remain private.

Never restore `MEDIA_URL` or public media serving.

Uploaded audio remains untrusted input and must continue through existing validation and private storage.

### Assessment

Assessment remains historical quality-control evidence.

Preserve:

- one assessment per booking;
- assigned approved teacher only;
- completed bookings only;
- exactly one score per active criterion;
- scores 1–5;
- historical rubric snapshots;
- immutable submitted teacher scores;
- separate lead review;
- server-side family scoping;
- missing assessments are missing data, never zero.

### Payout

Family pricing must not silently determine teacher payout.

Finalized payout history remains immutable.

Do not redesign payout logic during SaaS Phase 1.

## Testing policy

Manual testing remains the primary feedback loop during feature development.

Automated tests are required for durable organization invariants because tenant isolation is a high-risk security boundary.

SaaS Phase 1 automated coverage should focus on:

- organization creation;
- automatic owner membership;
- membership uniqueness;
- organization-level role validation;
- organization membership isolation;
- cross-organization read denial;
- owner/admin membership-management permissions;
- unauthorized membership-management denial;
- transaction rollback if organization creation cannot create the owner membership.

Do not generate huge unrelated test suites.

## Manual testing

The organization foundation should be manually verified through `/api/docs/`.

At minimum verify:

```text
User A
  -> creates Organization A
  -> becomes owner
  -> reads Organization A

User B
  -> remains outside Organization A
  -> cannot read Organization A

Owner A
  -> creates a staff/admin/teacher membership
  -> can read organization memberships

Member A
  -> can read Organization A
  -> cannot manage membership unless role permits it

User B
  -> cannot read or modify Organization A's membership
```

## Data integrity principles

Organization-level invariants must be enforced server-side.

Do not rely solely on frontend checks.

For relationships where consistency spans multiple models, use the established repository approach:

- model validation for domain invariants;
- API permission classes for access control;
- serializers for API input validation;
- database constraints where the invariant can be represented directly.

## No speculative tenancy

Do not add `organization_id` to:

- User
- TeacherProfile
- Track
- Level
- PlacementResult
- Availability
- Booking
- Cohort
- TeacherWaitlist
- PricingAgreement
- SessionAssessment
- ProgressSnapshot
- TeacherPayout

during SaaS Phase 1.

Those models will be handled in later bounded phases after the organization foundation has been proven.

If an implementation appears to require one of these fields earlier, stop and explain exactly why instead of quietly expanding scope.

## Source-of-truth rules

- `CLAUDE.md` — working rules and high-level phase status.
- `specs/` — detailed requirements for each phase.
- `specs/saas/` — detailed requirements for SaaS expansion phases.
- `docs/quran-academy-mvp-spec.md` — original product intent.
- `learnings.md` — decisions, discoveries, and surprising edge cases.
- `tech-debt.md` — deliberate shortcuts and deferred work.
- `README.md` — environment and deployment workflow.

Do not create competing requirement documents.

## Definition of done

A SaaS phase is done when:

- its acceptance criteria are implemented;
- `python manage.py check` passes;
- `python manage.py makemigrations --check` passes;
- targeted automated tests for changed invariants pass;
- the developer manually verifies the changed API journey;
- previous-phase behaviour remains intact;
- important architectural decisions are recorded in `learnings.md`;
- intentional shortcuts are recorded in `tech-debt.md`;
- the phase is committed before the next phase begins.

## Stop and ask instead of guessing

Stop when:

- the tenant boundary is ambiguous;
- an existing model's meaning would change;
- an existing permission would need to become less restrictive;
- the current `User.role` semantics would need to change;
- a user could belong to more than one organization and the correct behaviour is undefined;
- an existing database uniqueness rule conflicts with tenant-scoped uniqueness;
- a product decision is required;
- a financial, privacy, or security boundary would change;
- a new phase would be needed to implement the proposed behaviour safely.

## Session hygiene

- One phase, or one clearly bounded task, per chat session.
- Keep work small enough to manually verify.
- Commit completed tasks frequently.
- End each session at a clear checkpoint.
- Never carry unresolved product decisions into a later phase.
- Prefer the smallest next task that can be implemented and manually verified.