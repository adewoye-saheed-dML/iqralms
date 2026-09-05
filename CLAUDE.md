# Quran Academy Platform

Django REST backend for a Quran/Arabic teaching academy evolving into a **multi-tenant SaaS platform for Quran academies, Islamic schools, and related learning institutions**.

The overall product intent is in `docs/quran-academy-mvp-spec.md`.

Detailed requirements for each development stage live in `specs/`.

The original single-academy product phases established the existing domain behaviour. The SaaS expansion extends that system incrementally without casually rewriting completed business logic.

---

# Phase status

## Original product phases

- Phase 1 Accounts — DONE
- Phase 2 Curriculum & placement — DONE
- Phase 3 Scheduling — DONE
- Phase 3.5 Booking debt cleanup — DONE
- Phase 4 Routing — DONE
- Phase 5 Pricing & preferred-teacher waitlist — DONE
- Phase 6 Production hardening — DONE
- Phase 7 Assessment & progress — IMPLEMENTED; manual acceptance pending
- Phase 8 Payouts & statements — IMPLEMENTED; manual acceptance pending

## SaaS expansion

- SaaS Phase 1 Organization foundation — IMPLEMENTED; manual acceptance pending
- SaaS Phase 2 Accounts tenancy — NEXT

The first SaaS phase already introduced:

- `Organization`;
- `OrganizationMembership`;
- organization-level roles;
- membership status;
- organization creation;
- automatic owner membership;
- organization membership APIs;
- organization membership permissions;
- cross-tenant organization access protection.

Do not recreate or redesign those features in SaaS Phase 2.

A phase is not complete merely because code exists. Do not start the next phase until the current phase's acceptance criteria are verified and the completed work is committed.

---

# Product direction

The product is no longer treated as a system for one academy only.

The target product is:

> A multi-tenant operating platform for Quran and Islamic learning institutions.

The platform should eventually allow many independent academies to operate on the same application while keeping their users, curriculum, schedules, pricing, assessment, financial records, and administration isolated from one another.

Examples of target institutions:

- Quran academies;
- Hifz schools;
- Tajweed schools;
- Arabic learning institutions;
- Islamic learning centres;
- online Quran teaching businesses;
- small and medium Islamic education organizations.

The platform may eventually integrate with:

- WhatsApp;
- Telegram;
- email;
- Google Meet;
- Jitsi;
- other communication/video providers.

Those integrations are not part of the current backend tenancy phases.

---

# SaaS architecture

The fundamental boundary is:

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
    +-- Future integrations
```

A user is a global identity.

An organization membership represents that user's relationship with a specific academy.

```text
User
    =
global identity

OrganizationMembership
    =
relationship with one academy
```

A user may belong to multiple organizations.

For example:

```text
User A
    |
    +-- Academy A → teacher
    |
    +-- Academy B → admin
```

Do not introduce `User.organization`.

That would incorrectly force one user to belong to one academy.

---

# Existing account-role model

The existing `accounts.User.role` remains:

```text
lead
sub
student
parent
```

This is a global application role and already drives existing business logic.

Do not replace it with organization membership roles.

Organization roles are:

```text
owner
admin
staff
teacher
```

They answer a different question:

```text
User.role
    =
what kind of account is this?

OrganizationMembership.role
    =
what authority does this account have inside this academy?
```

The two are allowed to coexist during the migration.

Do not change `User.role` during SaaS Phase 2 unless the active phase specification explicitly requires it.

---

# Multi-tenancy migration sequence

The SaaS migration is incremental.

```text
SaaS Phase 1
Organization foundation
        |
        v
SaaS Phase 2
Accounts tenancy
        |
        v
SaaS Phase 3
Curriculum tenancy
        |
        v
SaaS Phase 4
Scheduling tenancy
        |
        v
SaaS Phase 5
Pricing tenancy
        |
        v
SaaS Phase 6
Assessment tenancy
        |
        v
SaaS Phase 7
Payout tenancy
        |
        v
SaaS Phase 8
Academy onboarding
        |
        v
SaaS Phase 9
Academy settings
        |
        v
SaaS Phase 10
Integrations
        |
        v
SaaS Phase 11
Tenant security audit
        |
        v
Frontend
```

The exact phase number may change if implementation reveals a safer decomposition, but the dependency order must not be casually reversed.

---

# Phase 1 foundation already implemented

The `organizations` app is now the tenant foundation.

Existing concepts include:

```text
Organization
OrganizationMembership
OrganizationRole
MembershipStatus
```

The organization API already establishes:

```text
POST  /api/organizations/
GET   /api/organizations/mine/
GET   /api/organizations/{id}/
GET   /api/organizations/{id}/memberships/
POST  /api/organizations/{id}/memberships/
PATCH /api/organizations/{id}/memberships/{member_id}/
```

Do not recreate these endpoints.

Do not add a second organization permission system.

Do not add a second ownership field to `Organization`.

Ownership is represented by the owner membership.

---

# SaaS Phase 2 — Accounts tenancy

SaaS Phase 2 is responsible for making the **accounts domain aware of organization membership**.

The goal is not yet to make every domain tenant-aware.

The phase focuses on:

- student membership;
- parent membership;
- teacher membership;
- parent/student relationships;
- organization-specific teacher configuration;
- account queries that need organization context;
- organization-aware account permissions.

The phase must preserve the existing account authentication model.

---

# Important existing account structures

Current `accounts.User` contains:

```text
role
timezone
date_of_birth
is_minor
signup_code
```

`ParentLink` currently connects:

```text
parent → student
```

and `TeacherProfile` currently contains:

```text
user
bio
max_weekly_hours
hourly_payout_rate
is_lead
approved
specialties
```

These models were originally designed for one academy.

SaaS Phase 2 must identify which of these attributes are global identity information and which are academy-specific.

---

# Global versus organization-specific account data

The following are global user identity attributes:

```text
User.username
User.email
User.first_name
User.last_name
User.password
User.timezone
User.date_of_birth
User.is_minor
User.signup_code
```

Do not duplicate these per organization.

The following concepts are potentially organization-specific:

```text
teacher approval
teacher capacity
teacher payout rate
lead/sub teaching status
academy teaching profile
academy-specific teacher configuration
```

Therefore the existing global `TeacherProfile` must not simply be copied into every organization without first deciding which fields belong to the tenant.

---

# TeacherProfile migration rule

Current `TeacherProfile` is a `OneToOneField` to `User`.

That is compatible with a single academy but is not sufficient as the final SaaS model if one teacher can work for multiple academies.

Do not blindly change it.

Before changing `TeacherProfile`, inspect every existing reference to:

```text
user.teacher_profile
TeacherProfile.objects
TeacherProfile
hourly_payout_rate
max_weekly_hours
approved
is_lead
specialties
```

The active Phase 2 specification defines the migration boundary.

A likely long-term model is:

```text
User
   |
   +-- OrganizationMembership
             |
             +-- Organization-specific teacher profile
```

But do not introduce the final name or structure merely because it seems convenient.

Choose the smallest migration that preserves existing behaviour and prepares the next phase.

---

# ParentLink migration rule

Current `ParentLink` is global.

A parent/student relationship is a real account relationship, but academy access to that relationship must not cross tenant boundaries.

For example:

```text
Parent P
Student S
```

may have a real parent-child relationship.

If:

```text
P ∈ Academy A
S ∈ Academy B
```

an Academy A operation must not automatically gain access to S's Academy B records.

Phase 2 must therefore define and test the relationship between:

```text
ParentLink
+
OrganizationMembership
```

before curriculum and scheduling become tenant-scoped.

Do not expose a student's academy-specific data merely because a global parent-child relationship exists.

---

# Signup code

The existing student signup-code mechanism remains.

The signup code is an account-level mechanism for identifying a student.

It must not become an authorization token for organization data.

Knowing:

```text
student.signup_code
```

does not grant access to:

- curriculum;
- bookings;
- assessments;
- pricing;
- payouts;
- organization membership.

Those permissions come from authenticated identity and organization membership.

---

# Account onboarding

Do not implement full academy invitation/onboarding yet.

Specifically out of scope:

- invitation emails;
- invitation tokens;
- invitation expiry;
- bulk student import;
- bulk teacher import;
- CSV onboarding;
- academy registration wizard;
- frontend onboarding;
- WhatsApp onboarding;
- Telegram onboarding.

Those belong to later SaaS onboarding work.

Phase 2 only establishes the account-side tenancy relationship required by those future workflows.

---

# Teacher role compatibility

Do not silently equate:

```text
User.role = sub
```

with:

```text
OrganizationMembership.role = teacher
```

They are different fields.

However, when an organization membership is explicitly intended to represent a teacher, the system must prevent impossible account relationships.

For example, a membership should not casually claim:

```text
OrganizationMembership.role = teacher
```

for an account that cannot act as a teacher under the current account model.

If this requires a product decision, stop and ask rather than guessing.

---

# Owner/admin membership versus teaching identity

An organization owner or admin is not automatically a teacher.

For example:

```text
User.role = student
OrganizationMembership.role = owner
```

is technically valid under Phase 1 because organization ownership and account role were deliberately separated.

Do not automatically create a teacher profile merely because someone owns an organization.

Likewise:

```text
OrganizationMembership.role = admin
```

does not make a user a teacher.

---

# Account query scoping

Any new account endpoint that operates inside an organization must establish:

```text
request.user
        +
organization
        +
active OrganizationMembership
```

before exposing organization-specific account information.

Do not trust:

```text
organization_id
role
user_id
```

from the client as proof of authorization.

The organization comes from the URL or another server-verified context.

The user's role inside that organization comes from `OrganizationMembership`.

---

# Cross-tenant rule

The central Phase 2 security invariant is:

```text
A user's relationship with Academy A
must not grant access to account relationships belonging to Academy B.
```

Tests must explicitly construct:

```text
Academy A
Academy B

Parent P
Student S
Teacher T
```

and verify isolation.

---

# Existing domain preservation

Do not tenant-scope these domains during Phase 2:

```text
curriculum/
scheduling/
pricing/
assessment/
payouts/
```

They will be handled in later SaaS phases.

If an account change requires a small compatibility change in another domain, make the smallest possible change and document why.

Do not rewrite business logic in those domains.

---

# Scheduling preservation

Do not change:

```text
Booking.clean()
route_session()
TeacherBookingLock
availability logic
cohort routing
weekly capacity logic
```

in Phase 2.

Teacher configuration that scheduling currently consumes must remain compatible until SaaS scheduling migration occurs.

---

# Curriculum preservation

Do not make:

```text
Track
Level
PlacementResult
```

organization-scoped during Phase 2.

Teacher specialties currently reference curriculum tracks.

Do not redesign that relationship during this phase.

The curriculum tenancy phase will resolve organization-specific track ownership and teacher-specialty relationships together.

---

# Pricing preservation

Do not modify:

```text
PricingAgreement
active_for()
```

during Phase 2.

Teacher payout configuration may eventually become organization-specific, but actual payout tenancy belongs to the later payout phase.

Do not make financial changes here.

---

# Assessment preservation

Do not modify:

```text
SessionAssessment
ProgressSnapshot
AssessmentCriterion
```

during Phase 2.

Account access changes must not weaken existing assessment privacy.

---

# Payout preservation

Do not modify payout calculation or finalized payout history during Phase 2.

Teacher payout rates are potentially organization-specific, but payout behaviour is handled in the dedicated payout tenancy phase.

---

# Testing policy

Manual testing remains the primary feedback loop during feature development.

Automated tests are required for high-risk account and tenant isolation invariants.

High-value Phase 2 tests include:

- users belonging to multiple organizations;
- organization-specific teacher membership;
- parent/student membership isolation;
- cross-tenant parent/student access denial;
- account-role and organization-role separation;
- organization-specific teacher configuration;
- suspended membership access denial;
- account endpoints refusing unauthorized organization access;
- existing account regression behaviour.

Do not generate large CRUD test suites.

---

# Development method

Before changing code:

1. Read the active SaaS Phase 2 specification.
2. Inspect the relevant accounts files.
3. Search for all references to the model/field being changed.
4. Identify existing invariants.
5. Check `git status`.
6. Implement one bounded task.
7. Add focused tests.
8. Run targeted checks.
9. Manually verify the changed API.
10. Update `learnings.md` if a new architectural decision was made.
11. Update `tech-debt.md` only for deliberate unresolved limitations.
12. Commit the task.

Do not implement the entire phase in one AI session.

---

# Preferred task prompt

A normal Claude Code session should look like:

> Implement SaaS Phase 2 task 2.1 only. Read the Phase 2 specification and inspect the existing accounts models, serializers, views, permissions, URLs, and tests relevant to task 2.1. Do not modify curriculum, scheduling, pricing, assessment, or payouts unless explicitly required by the task. Preserve all existing account invariants. Add focused regression tests. Run the targeted checks and report changed files and manual verification steps. Stop when task 2.1 is complete.

---

# Git discipline

Use small commits.

Suggested sequence:

```text
feat: add organization-aware account helpers

feat: scope parent student relationships to organizations

feat: add organization teacher profile

feat: add organization-aware account endpoints

test: verify account tenant isolation
```

Do not combine all account-tenancy work into one giant commit.

---

# Source of truth

- `CLAUDE.md` — development rules and high-level phase status.
- `specs/` — detailed original product requirements.
- `specs/saas/` — detailed SaaS requirements.
- `docs/quran-academy-mvp-spec.md` — original product intent.
- `learnings.md` — architectural decisions and discoveries.
- `tech-debt.md` — deliberate deferred work.
- `README.md` — environment and deployment workflow.

Do not create competing requirement documents.

---

# Definition of done

A SaaS phase is complete when:

- all acceptance criteria are implemented;
- organization boundaries are enforced server-side;
- targeted automated tests pass;
- `python manage.py check` passes;
- `python manage.py makemigrations --check` passes;
- relevant existing regression tests pass;
- the changed API journey is manually verified;
- previous product behaviour remains intact;
- architectural decisions are recorded;
- deliberate limitations are recorded;
- the phase is committed.

---

# Stop and ask instead of guessing

Stop when:

- the correct global-versus-organization ownership of account data is ambiguous;
- changing `User.role` appears necessary;
- changing the meaning of an existing account role is required;
- ParentLink needs a product decision;
- a teacher can belong to multiple academies and the correct profile semantics are unclear;
- an existing scheduling assumption would need to change;
- curriculum must become tenant-aware earlier than Phase 3;
- an existing permission would become less restrictive;
- an organization can access another organization's student or parent data;
- invitation/onboarding behaviour is required;
- financial behaviour would change;
- an existing historical record would change meaning.

Do not silently invent product behaviour.

---

# Session hygiene

- One phase, or one clearly bounded task, per session.
- Keep work small enough to manually verify.
- Commit completed tasks frequently.
- End each session at a clear checkpoint.
- Never carry unresolved product decisions into a later phase.
- Prefer the smallest next task that can be implemented and manually verified.