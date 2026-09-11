# Quran Academy Platform

Django REST backend for a multi-tenant SaaS platform for Quran academies, Islamic schools, Hifz schools, Tajweed schools, Arabic learning institutions, and related learning organizations.

The product intent is documented in:

```text
docs/quran-academy-mvp-spec.md
```

Detailed implementation requirements live under:

```text
specs/
```

---

# Current Phase Status

## Original product phases

- Phase 1 Accounts — DONE / ACCEPTED
- Phase 2 Curriculum & placement — DONE / ACCEPTED
- Phase 3 Scheduling — DONE / ACCEPTED
- Phase 3.5 Booking debt cleanup — DONE / ACCEPTED
- Phase 4 Routing — DONE / ACCEPTED
- Phase 5 Pricing & preferred-teacher waitlist — DONE / ACCEPTED
- Phase 6 Production hardening — DONE / ACCEPTED
- Phase 7 Assessment & progress — DONE / ACCEPTED
- Phase 8 Payouts & statements — DONE / ACCEPTED

## SaaS expansion

- SaaS Phase 1 Organization foundation — DONE / ACCEPTED
- SaaS Phase 2 Accounts tenancy — DONE / ACCEPTED
- SaaS Phase 3 Curriculum tenancy — DONE / ACCEPTED
- SaaS Phase 4 Scheduling tenancy — DONE / ACCEPTED
- SaaS Phase 5 Pricing tenancy — NEXT
- SaaS Phase 6 Assessment tenancy
- SaaS Phase 7 Payout tenancy
- SaaS Phase 8 Academy onboarding
- SaaS Phase 9 Academy settings
- SaaS Phase 10 Integrations
- SaaS Phase 11 Tenant security audit
- Frontend

The specification for the current phase is:

```text
specs/saas/SaaS Phase 5 — Pricing Tenancy.md
```

Do not start SaaS Phase 6 until Phase 5 implementation, tests, API/manual acceptance, documentation, migrations, and commit are complete.

---

# SaaS Architecture

The tenant boundary is:

```text
Organization
    |
    +-- Memberships
    +-- Account relationships
    +-- Curriculum
    +-- Scheduling
    +-- Pricing
    +-- Assessment
    +-- Payouts
```

A `User` is a global identity.

An `OrganizationMembership` represents the user's relationship with one academy.

A user may belong to multiple academies.

Never add:

```python
User.organization
```

That would incorrectly force one global user into one tenant.

Organization roles remain:

```text
owner
admin
staff
teacher
```

Global account roles remain:

```text
lead
sub
student
parent
```

These answer different questions.

```text
User.role
    =
what kind of account is this?

OrganizationMembership.role
    =
what authority does this user have inside this academy?
```

Do not replace either role system during Phase 4.

Use:

```python
organizations.active_membership()
```

as the canonical active-membership rule.

Do not create another membership-status implementation.

---

# Accepted SaaS Phase 1 Decisions

SaaS Phase 1 established:

- `Organization`
- `OrganizationMembership`
- organization roles
- membership status
- automatic owner membership
- organization creation
- organization membership APIs
- organization permissions
- cross-tenant membership protection

Do not recreate these.

Ownership is represented by the owner membership.

Do not add another owner field to `Organization`.

---

# Accepted SaaS Phase 2 Decisions

SaaS Phase 2 established:

- `accounts.tenancy`
- organization-aware account helpers
- organization-scoped account endpoints
- parent/student organization isolation
- `OrganizationTeacherConfiguration`
- account tenant-isolation tests

Settled rules:

1. `User` remains global.
2. Registration remains global.
3. Registration does not automatically create organization membership.
4. `ParentLink` remains global.
5. Organization-specific parent/student access requires both accounts to be active members of the academy.
6. `TeacherProfile` remains for compatibility.
7. Academy-specific teacher terms live in `OrganizationTeacherConfiguration`.
8. Academy authority and `User.role` are separate concepts.
9. Organization-scoped routes follow:

```text
/api/<domain>/organizations/<organization_id>/<resource>/
```

Do not recreate Phase 1 or Phase 2 infrastructure.

---

# Accepted SaaS Phase 3 Decisions

SaaS Phase 3 established:

- `Track.organization`
- `(organization, slug)` track uniqueness
- academy-owned curriculum
- organization-scoped curriculum APIs
- academy-safe placements
- academy-safe placement audio access
- `curriculum.TeacherTrack`
- curriculum tenant-isolation tests
- legacy curriculum migration support

Important settled rules:

## Track ownership

`Track.organization` is the stored curriculum tenant boundary.

Do not add redundant `organization` columns to `Level` or `PlacementResult`.

Their organization is derived through:

```text
Level
    -> Track
        -> Organization
```

and:

```text
PlacementResult
    -> Track
        -> Organization
```

## Teacher curriculum eligibility

Academy-specific teacher curriculum eligibility is:

```text
OrganizationMembership
        |
        +-- TeacherTrack
                |
                +-- Track
```

The invariant is:

```text
TeacherTrack.membership.organization
==
TeacherTrack.track.organization
```

A teacher may therefore teach different tracks in different academies.

## Legacy specialty field

`TeacherProfile.specialties` still exists only because scheduling has not yet migrated its readers.

Phase 4 owns that migration.

After Phase 4 scheduling code must not use:

```text
TeacherProfile.specialties
```

as the authority for whether a teacher may teach an academy track.

Do not remove the legacy field until all remaining consumers have been audited.

---

---

# Accepted SaaS Phase 4 Decisions

SaaS Phase 4 established:

- `Availability.organization` explicit foreign key
- `Booking`, `Cohort`, and `TeacherWaitlist` tenant derivation via `level.track.organization`
- Model `@property` helpers for read-only organization access (`booking.organization`, `cohort.organization`, `waitlist.organization`)
- Separation of tenant capacity (`OrganizationTeacherConfiguration.max_weekly_hours`) and global physical session overlap
- Global `TeacherBookingLock` race-proof double-booking prevention on PostgreSQL
- Teacher authority and eligibility through `active_membership()`, `OrganizationTeacherConfiguration.approved`, and `curriculum.TeacherTrack`
- Deprecation of `TeacherProfile.specialties`, `approved`, and `max_weekly_hours` as authority for scheduling
- Student and linked-parent tenant boundary validation on bookings and routing
- Organization-scoped scheduling API: `/api/scheduling/organizations/<organization_pk>/...`
- Retirement of legacy unscoped scheduling API endpoints
- Tenant-scoped scheduling permissions (`AcademyScopedView(OrganizationScopedMixin)`) and serializers (`AcademyScopedSerializerMixin`)
- Comprehensive adversarial tenant isolation test suite (`scheduling.tests.test_tenant_isolation`)
- Non-ambiguous legacy scheduling data migration (`scheduling.legacy`)

Important settled rules:

## Stored vs Derived Tenancy
- `Availability` has no curriculum relation, so it carries an explicit `organization` foreign key.
- `Booking`, `Cohort`, and `TeacherWaitlist` point to `Level`, whose organization is derived through:
  ```text
  Booking / Cohort / TeacherWaitlist
      -> Level
          -> Track
              -> Organization
  ```
  Redundant `organization` columns must never be added to these models.
- Model `@property` helpers `booking.organization`, `cohort.organization`, and `waitlist.organization` provide convenient read-only access.

## Global Physical Time vs Tenant Capacity
- A human teacher cannot physically be in two places at once. Session time overlap checks (`clashing_bookings()`) and concurrency control (`TeacherBookingLock`) are **global** across all organizations.
- Weekly workload capacity (`max_weekly_hours`) is **tenant-scoped** and evaluated per organization via `OrganizationTeacherConfiguration`. Cancelling gives capacity back. Cohort sessions count once toward capacity regardless of student count.

## Concurrency and Race Protection
- `TeacherBookingLock` is keyed globally on `teacher_id` (`User`) with `SELECT ... FOR UPDATE` in PostgreSQL.
- Overlap validation and booking writes must always run inside `transaction.atomic()` after acquiring the teacher lock.

## Teacher Curriculum Eligibility
- A teacher may only be booked, routed, or assigned to a cohort for levels belonging to tracks they are explicitly permitted to teach in that organization via `curriculum.TeacherTrack`.
- The teacher must also have an active membership in that organization and `approved=True` on `OrganizationTeacherConfiguration`.
- `TeacherProfile.specialties` is no longer read by scheduling logic.

## Student and Parent Tenancy
- A booking can only be made for a student who is an active member of the booking's organization.
- A parent can only book, route, cancel, or view waitlists for a child if both parent and child have active memberships in that organization, and are linked via `ParentLink`.

## URL and API Structure
- All scheduling API endpoints are scoped under:
  `/api/scheduling/organizations/<organization_pk>/...`
- Scoped serializers reject foreign level, teacher, and student IDs as non-existent (400 "does not exist").
- Legacy unscoped endpoints (`/api/scheduling/bookings/`, `/api/scheduling/availability/`, `/api/scheduling/route/`, `/api/scheduling/cohorts/`, `/api/scheduling/waitlist/`) are retired.

---

# Current Phase — SaaS Phase 5 Pricing Tenancy

The specification for the current phase will live under:

```text
specs/saas/SaaS Phase 5 — Pricing Tenancy.md
```

Do not start SaaS Phase 6 until Phase 5 implementation, tests, API/manual acceptance, documentation, migrations, and commit are complete.

---

# Working Session Discipline (applies to every phase from here on)

Large tenancy migrations burn hours and tokens when run as one unbroken session. Follow this for all phases:

- **One numbered task per session.** Start a new session per task rather than carrying one long session through the whole phase — old audit/read output sitting in context is dead weight once you move to a different task.
- **Stop after an audit task.** When a task says "no code changes" / "report the plan," hold to it literally — review the report before opening a coding session for the next task.
- **Don't run the full gate after every edit.** `manage.py check` / `makemigrations --check` / `migrate` / full `pytest` against PostgreSQL belong at the Acceptance task only. During development tasks, run just the relevant test module.
- **Commit after each task**, not only at the end of the phase. Small commits give a rollback point and a natural place to end a session.
- **For future phases: split their spec the same way Phase 4 was split** — a short `00-core.md` with the goal/architecture/invariants/out-of-scope/definition-of-done, plus one file per numbered task in the "Recommended Implementation Sequence." Do not let a phase spec live only as one large file that every session reloads in full.

---

# Phase Completion Rule

A phase is complete only when implementation + tests + fresh migrations + tenant isolation + manual/API acceptance + OpenAPI verification + documentation + commit are all done.

Only then update this file to mark the phase DONE / ACCEPTED and advance to the next phase, condensing settled decisions into a new `# Accepted SaaS Phase <N> Decisions` section.
