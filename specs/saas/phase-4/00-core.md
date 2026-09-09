# SaaS Phase 4 — Core (read every session)

> This is the only file every Phase 4 session must load in full, regardless of which task you are on. Task-specific files (4.1.md ... 4.11.md) each assume this context and add only what that task needs.

---

# 1. Current Architecture

The scheduling domain currently contains or depends on:

```text
Availability
TeacherBookingLock
Booking
Cohort
TeacherWaitlist
routing
```

Current teacher scheduling rules still consume legacy global account state:

```text
TeacherProfile.approved
TeacherProfile.max_weekly_hours
TeacherProfile.specialties
```

SaaS Phase 2 introduced academy-specific teacher terms:

```text
OrganizationMembership
        |
        +-- OrganizationTeacherConfiguration
                |
                +-- approved
                +-- max_weekly_hours
                +-- hourly_payout_rate
```

SaaS Phase 3 introduced academy-specific curriculum eligibility:

```text
OrganizationMembership
        |
        +-- TeacherTrack
                |
                +-- Track
```

Scheduling has not switched to those relations yet.

That migration is the primary responsibility of Phase 4.

---


# 2. Phase Responsibilities

Phase 4 owns:

- scheduling tenant ownership;
- organization-scoped availability;
- organization-safe direct bookings;
- organization-safe cohorts;
- organization-safe routing;
- organization-aware lead teacher resolution;
- organization-aware sub-teacher matching;
- academy-specific teacher approval;
- academy-specific weekly capacity;
- academy-specific teacher track eligibility;
- organization-safe preferred-teacher routing;
- organization-safe waitlists;
- organization-scoped scheduling APIs;
- scheduling tenant-isolation tests;
- migration of existing scheduling data where necessary.

Phase 4 does not own:

- `PricingAgreement` tenancy;
- pricing calculations;
- assessment tenancy;
- progress tenancy;
- payout calculations;
- academy onboarding;
- invitations;
- notifications;
- billing;
- frontend;
- recurring-class redesign.

---


# 3. Existing Behaviour That Must Survive

Phase 4 is a tenant migration.

It is not permission to rewrite scheduling behaviour.

Preserve:

```text
UTC storage
timezone conversion
availability coverage rules
booking overlap detection
TeacherBookingLock concurrency protection
booking status behaviour
cancellation behaviour
past-start protection
routing order
cohort-first routing
lead fallback
sub-teacher fallback
preferred-teacher behaviour
waitlist behaviour
weekly-cap enforcement
Jitsi room generation
group eligibility
cohort seat limits
```

Existing regression tests remain authoritative unless tenant isolation requires a specifically documented change.

---


# 4. Target Scheduling Ownership Model

Do not add an `organization` field to every scheduling table automatically.

Use derived ownership when it is unambiguous.

Target:

```text
Organization
   |
   +-- Availability
   |
   +-- Track
         |
         +-- Level
               |
               +-- Booking
               +-- Cohort
               +-- TeacherWaitlist
```

Therefore:

```text
Booking.organization
    = Booking.level.track.organization

Cohort.organization
    = Cohort.level.track.organization

TeacherWaitlist.organization
    = TeacherWaitlist.level.track.organization
```

Those models should normally derive organization instead of storing a duplicate tenant column.

Availability has no Level or Track relationship and therefore requires an explicit academy boundary.

---


---

# 65. Out of Scope

Do not implement in Phase 4:

```text
PricingAgreement tenancy
academy-specific pricing
assessment tenancy
ProgressSnapshot tenancy
payout tenancy
academy billing
subscriptions
invitations
academy onboarding
branches
notifications
WhatsApp
Telegram
Google Meet
frontend
recurring cohorts
AI routing
new organization roles
lead/owner role unification
```

Do not pull Phase 5 work forward merely because TeacherWaitlist interacts with pricing elsewhere.

---


---

# 67. Definition of Done

SaaS Phase 4 is complete when:

```text
Availability is academy-owned
Booking is academy-safe
Cohort is academy-safe
Routing is academy-safe
Waitlist is academy-safe

TeacherTrack is scheduling authority for curriculum eligibility

OrganizationTeacherConfiguration is scheduling authority for:
  approved
  max_weekly_hours

weekly capacity is academy-specific

teacher physical overlap remains global

TeacherBookingLock remains concurrency-safe

parent/student tenant isolation holds

all scheduling APIs are tenant-safe

cross-academy object-id attacks are tested

existing scheduling behaviour still passes

fresh PostgreSQL migrations pass

OpenAPI is correct

manual acceptance passes

learnings.md is updated

tech-debt.md is updated

CLAUDE.md marks Phase 4 DONE / ACCEPTED

changes are committed
```

Only after that may development move to:

```text
SaaS Phase 5 — Pricing Tenancy
```
