# Quran Academy Platform

Django REST backend for a multi-tenant SaaS platform for Quran academies, Islamic schools, Hifz schools, Tajweed schools, Arabic learning institutions, and related learning organizations.

The product intent is documented in `docs/quran-academy-mvp-spec.md`.
Detailed requirements live under `specs/`.

## Current Phase Status

### Original product phases

- Phase 1 Accounts — DONE / ACCEPTED
- Phase 2 Curriculum & placement — DONE / ACCEPTED
- Phase 3 Scheduling — DONE / ACCEPTED
- Phase 3.5 Booking debt cleanup — DONE / ACCEPTED
- Phase 4 Routing — DONE / ACCEPTED
- Phase 5 Pricing & preferred-teacher waitlist — DONE / ACCEPTED
- Phase 6 Production hardening — DONE / ACCEPTED
- Phase 7 Assessment & progress — DONE / ACCEPTED
- Phase 8 Payouts & statements — DONE / ACCEPTED

### SaaS expansion

- SaaS Phase 1 Organization foundation — DONE / ACCEPTED
- SaaS Phase 2 Accounts tenancy — DONE / ACCEPTED
- SaaS Phase 3 Curriculum tenancy — DONE / ACCEPTED
- SaaS Phase 4 Scheduling tenancy — NEXT
- SaaS Phase 5 Pricing tenancy
- SaaS Phase 6 Assessment tenancy
- SaaS Phase 7 Payout tenancy
- SaaS Phase 8 Academy onboarding
- SaaS Phase 9 Academy settings
- SaaS Phase 10 Integrations
- SaaS Phase 11 Tenant security audit
- Frontend

**Write `specs/saas/SaaS Phase 4 — Scheduling Tenancy.md` before beginning Phase 4.** Every SaaS phase so far has had a specification to read first, and Phase 4 is the one that must switch scheduling from the legacy global `TeacherProfile.specialties` to the academy-scoped `curriculum.TeacherTrack` — a change to accepted booking and routing behaviour, which is not something to design while writing it.

A phase is not complete merely because code exists. Implementation, automated tests, manual/API acceptance where specified, documentation, and a commit are all part of completion.

---

# SaaS Architecture Rules

The fundamental tenant boundary is:

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

A user may belong to multiple organizations.

**Never add `User.organization`.**

Organization roles are:

```text
owner
admin
staff
teacher
```

Existing global account roles remain:

```text
lead
sub
student
parent
```

Do not replace one role system with the other.

Use `organizations.active_membership()` as the canonical active-membership check. Do not create another membership-status implementation.

---

# Accepted SaaS Phase 1 Decisions

SaaS Phase 1 established:

- `Organization`
- `OrganizationMembership`
- organization roles
- membership status
- owner membership
- organization creation
- organization membership APIs
- organization membership permissions
- cross-tenant organization access protection

Do not recreate or redesign these.

Ownership is represented by the owner membership. Do not add a second organization owner field.

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
2. Registration remains global and does not automatically create organization membership.
3. `ParentLink` remains a global family relationship.
4. Academy-specific parent/student access requires both accounts to be active members of that academy.
5. `TeacherProfile` remains unchanged for compatibility.
6. Academy-specific teacher terms live in `OrganizationTeacherConfiguration`.
7. `TeacherProfile.specialties` remains coupled to the old global `Track` model until SaaS Phase 3 migrates curriculum ownership.
8. Organization-scoped routes use:

```text
/api/<domain>/organizations/<organization_id>/<resource>/
```

9. Organization membership authority and `User.role` are separate concepts.
10. Onboarding, invitations, billing, and frontend are later concerns.

Do not recreate or redesign Phase 1/2 infrastructure in a later phase.

---

# Accepted SaaS Phase 3 Decisions

SaaS Phase 3 established:

- `Track.organization`, with `(organization, slug)` uniqueness
- `curriculum.TeacherTrack`, anchored to `OrganizationMembership`
- organization-scoped curriculum, level, placement and teacher-track endpoints
- academy-safe placement submission, review and private audio access
- `curriculum/legacy.py`, the pre-SaaS curriculum backfill
- curriculum tenant-isolation and cross-academy teacher tests

Settled rules:

1. `Track.organization` is the **only** stored tenant column in the domain.
   `Level.organization` and `PlacementResult.organization` are properties that read
   through the track, and every queryset joins rather than reading a local copy. Do
   not add a second organization column to a curriculum model.
2. Track slug uniqueness is `(organization, slug)`. Two academies may both teach
   `tajweed`; one academy may not.
3. A track's academy is immutable after creation (`Track.clean()`). Transferring
   curriculum between academies is a separate product decision, not a field edit.
4. Levels are still appended, never inserted. The API does not accept `order` at
   all — the server computes `max(order)+1` — and an existing level's order and
   track are not editable.
5. A placement's academy is its track's. `PlacementResult.clean()` requires the
   student, and the reviewer, to be **active members** of that academy;
   `PlacementResult.objects.in_organization()` is the one queryset that expresses
   it, and it filters on the track *and* the student's membership.
6. Placement review is still lead-only, and now also requires active membership in
   the placement's academy.
7. Curriculum authoring is `owner` and `admin`
   (`curriculum.permissions.CURRICULUM_MANAGER_ROLES`). Every active member may
   read the curriculum; the teacher-track roster is owner/admin only, and a teacher
   reads their own from `teachers/mine/`.
8. Teacher curriculum eligibility is `TeacherTrack(membership, track, active)`, and
   `membership.organization` must equal `track.organization`. One teacher holds
   independent eligibility per academy.
9. `TeacherProfile.specialties` is untouched and is **still the relation scheduling
   enforces**. Do not remove it before SaaS Phase 4 has migrated those readers.
10. The Phase 2 global curriculum routes are retired. The only global route left is
    the token-gated placement-audio download, and it is global because the token —
    not the path — authorises it.
11. Pre-SaaS curriculum is assigned by `curriculum/legacy.py`, which resolves the
    owning academy or refuses with instructions. It never creates an academy, and it
    never alters an existing membership.

Do not recreate or redesign Phase 1/2/3 infrastructure in a later phase.

---

# Current Phase — SaaS Phase 4 Scheduling Tenancy

No specification has been written yet. Write
`specs/saas/SaaS Phase 4 — Scheduling Tenancy.md` first, and read it before editing
code.

What Phase 3 left on Phase 4's doorstep, in the order it will matter:

- `scheduling.models.specialty_error`, `Booking.clean()`, `Cohort.clean()` and
  `scheduling.routing` all still read `TeacherProfile.specialties`, which is global.
  They should read `TeacherTrack.objects.in_organization(...).active()` instead,
  resolving the teacher's membership from the booking's academy.
- `scheduling/serializers.py` accepts `Level.objects.all()`. It needs the academy
  from the route and `curriculum.serializers.levels_in()`.
- `scheduling/permissions.py` still equates `User.role == lead` with academy
  authority. Convert it the way `curriculum/permissions.py` was converted: pair the
  account role with `organizations.permissions.IsOrganizationMember` and scope the
  queryset.

`tech-debt.md` carries the full list with the reasoning.
---

# Security Rules

Never authorize academy data from object ids alone.

Every academy-scoped request must establish:

```text
request.user
+
organization
+
active membership
```

Then query only records within that organization.

Never trust client-supplied:

```text
organization_id
user_id
role
```

as proof of authorization.

Tenant isolation must exist in:

- model/service validation;
- querysets;
- permissions;
- serializers where appropriate;
- automated regression tests.

A serializer hiding an organization field is not tenant isolation.

---

# Parent Access

`ParentLink` remains global.

Academy-specific parent access requires:

```text
parent active in organization
AND
student active in organization
AND
ParentLink exists
```

Reuse `accounts.tenancy.children_in_organization()`.

Do not duplicate the parent/student tenancy rule.

---

# Audio Security

Preserve Phase 6 private placement audio behaviour.

Never restore public media URLs.

Authorization becomes:

```text
authenticated user
        |
        v
active organization membership
        |
        v
placement belongs to organization
        |
        v
existing Phase 6 audio authorization
        |
        v
short-lived signed URL
```

This is now implemented (`curriculum.views.AcademyPlacementAudioURLView`). The
roles that may hear a sample are the lead teacher and the student themselves —
**not** sub-teachers, and not a minor's linked parent, who may read the placement
but not the recording. Every phase from here inherits that rule; widening it is a
product decision with its own tests to change.

---

# Existing Domain Preservation

These domains have **no tenant boundary yet**, and each has a phase of its own:

```text
scheduling/   SaaS Phase 4
pricing/      SaaS Phase 5
assessment/   SaaS Phase 6
payouts/      SaaS Phase 7
```

Migrate one per phase. Do not rewrite, outside the phase that owns it:

```text
Booking.clean()
route_session()
TeacherBookingLock
availability logic
cohort routing
weekly capacity logic
PricingAgreement
SessionAssessment
ProgressSnapshot
payout calculations
```

Make only the smallest compatibility changes required. SaaS Phase 3 needed **none**
at all: `Track` and `Level` became organization-owned without any of these files
changing, because they reach curriculum through foreign keys that still resolve.

That is also the trap. Those apps resolve `Track` and `Level` *globally* — a caller
who knows an id can name another academy's level in a booking or an agreement — and
that reachability is now visible in a way it was not before. It is each domain's own
phase to close, not a Phase 3 omission; `tech-debt.md` records it.

---

# Migration Discipline

For existing pre-SaaS data:

- do not invent academies;
- do not duplicate users;
- assign old data to the determinable existing academy context;
- preserve primary keys where practical;
- keep referential integrity during migrations;
- do not make tenant ownership permanently nullable;
- document any ambiguous legacy mappings in `learnings.md`;
- record accepted shortcuts or deferred cleanup in `tech-debt.md`.

Do not perform destructive data migrations merely to simplify code.

**`curriculum/legacy.py` is the pattern to reuse.** SaaS Phase 3 hit the case where
the answer was not determinable — existing curriculum and no organization at all —
and settled it as *resolve or refuse*:

```text
nothing unowned                                ->  no-op
exactly one organization                       ->  that academy
SAAS_LEGACY_CURRICULUM_ORGANIZATION=<pk|slug>  ->  the academy it names
anything else                                  ->  raise, with instructions
```

Three properties are worth copying. The decision lives in an importable module
rather than inside the migration, so it is unit-tested. The migration is a no-op on
a fresh database, which is the path every deployment and the test database take. And
it never creates an academy and never edits an existing membership — in particular a
suspended member stays suspended, because a migration quietly restoring revoked
access is worse than one that does too little.

A tenancy phase whose backfill needs users to hold memberships should admit only the
people who *already hold* that domain's data, and say so in `learnings.md`.

---

# Testing Gate

Every tenancy phase inherits this gate. Before it is marked complete, prove:

1. every existing test still passes — the original Phase 1–8 suite and each accepted
   SaaS phase's;
2. fresh PostgreSQL migrations succeed on an empty database;
3. `python manage.py check` passes;
4. `python manage.py makemigrations --check` passes;
5. tenant isolation is tested for every model the phase made academy-owned;
6. object-id attacks across academies are rejected — detail and mutation endpoints,
   not only listings;
7. private placement audio remains isolated;
8. OpenAPI/Swagger exposes the final organization-scoped API;
9. manual/API acceptance for the phase's journey is completed;
10. the phase is committed before the next one begins.

SaaS Phase 3 met this gate on 2026-09-05: 1439 tests pass (1301 before the phase),
fresh migrations apply to an empty PostgreSQL database, `check` and
`makemigrations --check` are clean, the schema documents all thirteen
academy-scoped curriculum paths, and the two-academy journey in §31 of the phase
spec was walked over HTTP.

---

# Coding Rules

Prefer the existing repository patterns over introducing new frameworks.

Before changing an existing model:

1. inspect all consumers;
2. inspect tests;
3. inspect migrations;
4. inspect permissions and querysets;
5. make the smallest safe change;
6. add regression tests before declaring the phase complete.

Do not use `bulk_create()` where it bypasses required invariants.

Where an invariant spans related rows or tables, enforce it in domain/model/service logic as appropriate, not only in serializers.

Do not silently change completed product behaviour.

When a specification says "stop and ask", do not guess.

---

# Phase Completion Rule

Do not update this file to move to the next phase until:

- all of the current phase's acceptance criteria pass;
- manual/API acceptance is complete;
- documentation is updated;
- technical debt is recorded;
- changes are committed;
- the repository is in a reproducible state on PostgreSQL.
