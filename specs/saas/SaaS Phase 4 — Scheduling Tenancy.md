# SaaS Phase 4 — Scheduling Tenancy

## Goal

Make the existing `scheduling` domain organization-aware without breaking the accepted scheduling, routing, cohort, capacity, waitlist, timezone, or concurrency behaviour.

This phase establishes the academy boundary for:

```text
Availability
Booking
Cohort
Routing
Teacher scheduling eligibility
Weekly teacher capacity
TeacherWaitlist
Scheduling APIs
```

The outcome is:

> Each academy schedules only its own students, teachers, curriculum, availability, cohorts and waitlist requests, while a global teacher may work for multiple academies without tenant data leaking between them or being physically double-booked.

This phase consumes the tenancy infrastructure established by SaaS Phases 1–3.

It must not migrate pricing, assessment, or payouts.

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

# 5. Availability Ownership

Current:

```text
Availability
    teacher
    weekday
    start_time_utc
    end_time_utc
```

This is global.

It cannot represent one teacher publishing different hours to different academies.

Phase 4 must make Availability academy-specific.

Minimum final semantics:

```text
organization
teacher
weekday
start_time_utc
end_time_utc
```

or an equivalent membership-anchored design consistent with existing repository patterns.

Whichever implementation is chosen must guarantee:

```text
teacher has active membership in availability.organization
```

at creation time.

A suspended membership must not make that academy's availability usable.

Do not duplicate availability into unrelated academies.

---

# 6. Availability Example

Teacher T belongs to both academies.

```text
Academy A:
Monday 08:00–12:00

Academy B:
Monday 14:00–18:00
```

Then:

```text
routing in Academy A
```

may only consume:

```text
08:00–12:00
```

and:

```text
routing in Academy B
```

may only consume:

```text
14:00–18:00
```

An Academy A booking may not succeed merely because Academy B has an availability window covering the requested time.

---

# 7. Timezone Behaviour

Preserve the existing scheduling rule:

```text
stored scheduling times = UTC
display/input conversion = user timezone
```

Do not store academy-local timestamps.

An academy is not a timezone boundary for user identity.

Teachers, parents and students continue to render scheduling data in the appropriate user timezone according to the accepted scheduling rules.

---

# 8. Booking Ownership

Do not require a duplicate `Booking.organization` field by default.

A Booking's academy is:

```text
Booking.level.track.organization
```

This must become the tenant context for validating:

```text
student
teacher
teacher configuration
teacher track eligibility
availability
weekly capacity
cohort
```

A booking combining records from different academies must fail.

---

# 9. Booking Student Rule

For a direct or routed academy booking:

```text
booking.student.role == student
AND
active membership exists for booking.organization
```

must hold.

A global student account does not automatically belong to every academy.

A suspended student cannot receive a new academy booking.

---

# 10. Parent Booking Rule

`ParentLink` remains global.

For a parent to book for a student:

```text
parent active in organization
AND
student active in organization
AND
ParentLink(parent, student)
```

must all be true.

Reuse:

```python
accounts.tenancy.children_in_organization()
```

Do not reimplement the relationship rule in scheduling.

Example:

```text
Parent P belongs to Academy A and Academy B
Student S belongs only to Academy B
ParentLink(P, S) exists
```

Then:

```text
P booking S through Academy B -> potentially allowed
P booking S through Academy A -> denied
```

The global ParentLink is not a tenant-access bypass.

---

# 11. Teacher Membership Rule

A teacher is eligible to participate in scheduling only if:

```text
teacher.is_teacher
AND
active OrganizationMembership exists
```

for the target academy.

Being a teacher globally is not sufficient.

Example:

```text
Teacher T
active in Academy A
not a member of Academy B
```

must produce:

```text
booking in Academy A -> potentially valid
booking in Academy B -> denied
```

---

# 12. Organization Teacher Configuration

Phase 2 established:

```text
OrganizationTeacherConfiguration
```

with:

```text
approved
max_weekly_hours
hourly_payout_rate
```

Phase 4 must consume:

```text
approved
max_weekly_hours
```

from this academy-specific configuration.

Do not use:

```text
TeacherProfile.approved
TeacherProfile.max_weekly_hours
```

as scheduling authority after this phase.

`hourly_payout_rate` is deliberately not migrated here.

It belongs to SaaS Phase 7 Payout tenancy.

---

# 13. Approval

Existing scheduling checks global:

```text
TeacherProfile.approved
```

Replace this with academy-specific approval.

Required:

```text
active teacher membership
AND
OrganizationTeacherConfiguration exists
AND
OrganizationTeacherConfiguration.approved == true
```

Example:

```text
Teacher T

Academy A -> approved
Academy B -> unapproved
```

Then:

```text
T can be booked by A
T cannot be booked by B
```

assuming all other scheduling requirements are satisfied.

---

# 14. Curriculum Eligibility

Existing scheduling checks:

```text
TeacherProfile.specialties
```

Phase 3 established:

```text
TeacherTrack
```

Phase 4 must migrate all scheduling eligibility reads to:

```text
TeacherTrack
```

Required:

```text
TeacherTrack.membership.user == teacher
TeacherTrack.membership.organization == booking.organization
TeacherTrack.track == booking.level.track
TeacherTrack.active == true
```

A track assignment in Academy A grants no eligibility in Academy B.

---

# 15. `specialty_error()`

Audit the existing:

```python
scheduling.models.specialty_error()
```

Do not create a second competing eligibility implementation.

Refactor it, or replace it with a clearly named academy-aware equivalent, so direct booking, cohort creation and routing continue to ask the same domain question.

The new logical signature should have enough context to answer:

```text
can this teacher teach this level in this academy?
```

The academy should normally derive from:

```text
level.track.organization
```

rather than trusting a client-provided organization.

---

# 16. `bookable_teacher_error()`

Current booking eligibility relies on the global teacher profile.

Refactor the rule so the booking's academy controls approval.

The academy-aware rule should verify at least:

```text
global account is teacher-type
active organization membership exists
organization teacher configuration exists
configuration is approved
```

Retain any still-valid requirement for the global TeacherProfile only when another accepted behaviour genuinely depends on it.

Do not keep global approval as a hidden fallback.

Missing academy configuration should not silently use global teacher terms.

---

# 17. Weekly Capacity

Existing weekly capacity uses:

```text
TeacherProfile.max_weekly_hours
```

Phase 4 must use:

```text
OrganizationTeacherConfiguration.max_weekly_hours
```

for the target academy.

Capacity consumption is academy-specific.

Example:

```text
Teacher T

Academy A cap = 10 hours
Academy B cap = 5 hours
```

If T has used:

```text
8 hours in Academy A
1 hour in Academy B
```

remaining capacity is:

```text
Academy A = 2 hours
Academy B = 4 hours
```

Do not sum Academy A's booking minutes into Academy B's contracted weekly cap.

---

# 18. Global Physical Overlap

Weekly contracted capacity and physical time collision are not the same rule.

A teacher is one person.

Therefore:

```text
Academy A:
Teacher T
Monday 10:00–10:30

Academy B:
Teacher T
Monday 10:00–10:30
```

must still conflict.

Do not tenant-scope booking overlap checks in a way that allows simultaneous sessions across academies.

The existing `TeacherBookingLock` should remain global per teacher.

Its purpose is to serialize booking writes for the human teacher, not for an academy.

---

# 19. TeacherBookingLock

Do not create:

```text
one TeacherBookingLock per academy
```

unless a proven implementation requirement forces it.

The preferred invariant remains:

```text
one lock per teacher
```

because conflicting writes may originate from separate organizations.

All booking creation paths must continue through the accepted locking mechanism.

Do not use:

```python
bulk_create()
```

for booking writes.

---

# 20. Booking.clean()

`Booking.clean()` remains the source of truth for booking-domain validity.

Phase 4 should adapt existing rules rather than duplicate them into API serializers.

It must preserve:

```text
teacher validity
teacher approval
track eligibility
availability coverage
teacher overlap
weekly capacity
student validity
cohort consistency
past-start rule
```

while making every academy-sensitive rule derive from:

```text
Booking.level.track.organization
```

A direct ORM write must not be able to bypass tenant consistency merely because it does not pass through a serializer.

---

# 21. Cohort Ownership

A Cohort's academy is:

```text
Cohort.level.track.organization
```

Do not add redundant ownership unless required.

The cohort teacher must:

```text
be a teacher account
be an active member of the cohort academy
have approved organization teacher configuration
have active TeacherTrack eligibility for cohort.level.track
```

Preserve:

```text
group_eligible
max_students
schedule_start_utc
seat rules
```

---

# 22. Cohort Isolation

Academy A may not:

- list Academy B's open cohorts;
- route a student into Academy B's cohort;
- create a cohort using Academy B's Level;
- assign an Academy B-only teacher;
- use Academy B availability.

Object ids must not bypass this.

---

# 23. Routing Context

The routing entry point must receive or derive a verified organization context.

Preferred conceptual call:

```python
route_session(
    organization=organization,
    student=student,
    level=level,
    ...
)
```

or equivalent repository-consistent design.

Do not infer the route academy from whichever teacher happens to be selected.

The academy is determined before candidate selection.

The requested level must belong to the route academy.

---

# 24. Routing Order

Preserve the accepted routing algorithm:

```text
1. open matching cohort
2. lead teacher
3. matched sub-teacher
4. no capacity
```

Preferred-teacher requests continue to use their existing separate path.

Phase 4 changes candidate scope, not routing product logic.

Do not introduce:

- weighted ranking;
- ML matching;
- arbitrary teacher scoring;
- cross-academy fallback.

---

# 25. Cohort Routing

Cohort-first routing must search only:

```text
Cohort.level.track.organization == organization
```

A valid open cohort from another academy is invisible to the routing request.

The existing group eligibility and start-time tolerance rules remain unchanged.

---

# 26. Lead Teacher Routing

Current routing searches globally for the lead teacher.

That is no longer safe.

Lead candidate resolution must be academy-scoped.

Required candidate conditions:

```text
User.role == lead
active membership in organization
approved OrganizationTeacherConfiguration
active TeacherTrack for requested track
```

Use deterministic ordering where the existing code requires it.

Do not let:

```text
Academy A routing
```

select:

```text
Academy B lead
```

merely because that user is globally `role=lead`.

---

# 27. Organization Authority vs Lead Account Role

Do not confuse:

```text
OrganizationMembership.role
```

with:

```text
User.role
```

An organization owner is not automatically the pedagogical lead teacher.

An admin is not automatically the lead teacher.

The accepted lead scheduling rule remains based on the global account type during this migration:

```text
User.role == lead
```

plus active membership and academy-specific teaching configuration.

A future role redesign is separate work.

---

# 28. Sub-Teacher Routing

Current matching uses global:

```text
TeacherProfile.specialties
```

Replace it with tenant-aware TeacherTrack queries.

Matched sub-teacher candidate:

```text
User.role == sub
active organization membership
approved organization configuration
active TeacherTrack for requested track
```

Then existing availability, clash and capacity rules determine whether the teacher can take the slot.

---

# 29. Preferred Teacher

Preserve the Phase 5 original-product behaviour:

```text
preferred teacher specified
    |
    +-- try that teacher only
    |
    +-- if temporarily unavailable/full -> waitlist
    |
    +-- hard eligibility failure -> reject
```

Do not silently fall through to another teacher.

Add academy validation:

```text
preferred teacher active in organization
approved there
eligible for requested track there
```

A teacher known to Academy B must not be requestable through Academy A merely because the global user id exists.

---

# 30. Waitlist Ownership

`TeacherWaitlist` belongs to:

```text
TeacherWaitlist.level.track.organization
```

The following must belong to that academy:

```text
student
requested_teacher
level
```

The requested teacher must satisfy the academy teacher rules.

The student must satisfy academy membership rules.

Do not migrate pricing agreements in this phase.

---

# 31. Waitlist Promotion

Waitlist promotion must remain tenant-safe.

Before promotion verify:

```text
waitlist belongs to organization
student remains eligible for organization
teacher remains eligible for organization
teacher configuration remains approved
TeacherTrack remains active
availability/capacity/overlap still permit booking
```

Create the promoted Booking through the normal Booking path.

Do not bypass `Booking.clean()` or `TeacherBookingLock`.

---

# 32. Scheduling Permissions

Current scheduling permission classes mostly answer global account-type questions.

Keep those where useful, but add organization membership authorization.

For every academy-scoped scheduling endpoint:

```text
authenticated
AND
active membership
AND
appropriate account role
```

must hold.

Examples:

### Student booking

```text
User.role == student
AND
active organization membership
```

### Parent booking

```text
User.role == parent
AND
active organization membership
AND
student accessible through children_in_organization()
```

### Teacher schedule

```text
user.is_teacher
AND
active organization membership
```

### Lead cohort creation

```text
User.role == lead
AND
active organization membership
```

Do not use global role alone as academy authorization.

---

# 33. Scheduling API

Replace the global tenant-assuming surface with organization-scoped routes.

Target convention:

```text
/api/scheduling/organizations/{organization_id}/...
```

Equivalent resource set:

```text
availability
bookings
bookings/mine
bookings/teaching
bookings/{id}/cancel
route
cohorts
cohorts/open
waitlist/mine
waitlist/for-teacher
waitlist/{id}/promote
```

Exact Django route names may follow repository conventions.

---

# 34. Legacy Global Routes

Current scheduling routes are global.

Audit all clients/tests before removing them.

The Phase 4 desired end state is no global endpoint that can accidentally operate across multiple academies.

Preferred transition:

```text
old global scheduling endpoints -> retire safely
new academy-scoped endpoints -> canonical
```

Do not leave an endpoint that simply runs:

```python
Booking.objects.all()
Availability.objects.all()
Cohort.objects.all()
TeacherWaitlist.objects.all()
```

across tenants.

---

# 35. Organization Route Context

The URL:

```text
/api/scheduling/organizations/{organization_id}/...
```

provides tenant context.

It does not itself grant access.

Every view must verify:

```text
active_membership(request.user, organization)
```

using the accepted organization infrastructure.

Do not accept request-body `organization_id` as a replacement.

---

# 36. Serializer Level Querysets

Current scheduling serializers use global:

```python
Level.objects.all()
```

This must be removed from academy-scoped scheduling writes.

Resolve Levels only from the route academy.

Reuse the curriculum tenancy mechanisms established during SaaS Phase 3.

Required:

```text
Academy A route
+
Academy B Level id
=
rejected
```

The foreign id must not become usable merely because it exists.

---

# 37. Serializer Teacher Querysets

Direct booking and preferred-teacher fields must not blindly expose every User as an academy teacher.

The serializer or domain resolver must verify:

```text
teacher has active membership in route academy
teacher is valid teacher account
```

Do not treat user-id existence as academy participation.

Hard business eligibility may continue to be surfaced through model validation where that preserves existing error semantics.

---

# 38. Serializer Student Querysets

Parent requests must not accept every global student.

Use academy-filtered student resolution.

Reuse:

```python
children_in_organization()
```

for parent operations.

Student callers continue to act only for themselves.

A parent must not be able to probe another academy's student ids.

---

# 39. Queryset Helpers

Prefer domain helpers/managers where multiple scheduling views need the same tenant filter.

Examples conceptually:

```python
Booking.objects.in_organization(organization)
Cohort.objects.in_organization(organization)
TeacherWaitlist.objects.in_organization(organization)
Availability.objects.in_organization(organization)
```

Use repository-consistent manager/queryset patterns.

Do not duplicate complex tenant predicates across every view.

---

# 40. Booking Queryset

An organization-scoped booking queryset should derive through curriculum:

```python
Booking.objects.filter(
    level__track__organization=organization
)
```

Then apply user-specific access:

```text
student -> own bookings
parent -> organization-linked children's bookings where supported
teacher -> own teaching bookings
authorized administrative actor -> only if an existing endpoint requires it
```

Do not return every booking in an academy unless a real product endpoint requires that.

---

# 41. Cohort Queryset

Tenant scope:

```python
Cohort.objects.filter(
    level__track__organization=organization
)
```

Open-cohort routing/listing must remain within this queryset.

Do not query globally and filter after serialization.

---

# 42. Waitlist Queryset

Tenant scope:

```python
TeacherWaitlist.objects.filter(
    level__track__organization=organization
)
```

Then apply the existing family/teacher visibility rule.

Tenant isolation belongs in the query, not only in response filtering.

---

# 43. Availability Queryset

Availability must have an explicit tenant relationship after migration.

All lookups must include that academy context.

A teacher's availability listing in Academy A must not disclose Academy B hours.

---

# 44. Suspended Membership

Suspending membership must immediately stop new academy scheduling access.

Required outcomes:

```text
suspended student -> cannot create new booking
suspended parent -> cannot book for child
suspended teacher -> not a routing candidate
suspended teacher -> availability not usable
suspended lead -> cannot create cohorts
suspended teacher -> cannot promote waitlist entries
```

Existing historical records do not need to be deleted.

Suspension is an access state, not data destruction.

---

# 45. Existing Historical Bookings

Do not make existing historical bookings unsaveable merely because:

- membership was later suspended;
- teacher configuration changed;
- TeacherTrack was later disabled;
- weekly cap was later lowered.

Preserve the existing scheduling pattern where creation-time eligibility changes do not retroactively invalidate accepted bookings.

Tenant ownership itself must still remain internally consistent.

---

# 46. Cross-Tenant Object-ID Protection

Build adversarial tests for valid ids from another academy.

Examples:

```text
Academy A route + Academy B Level id
Academy A route + Academy B Booking id
Academy A route + Academy B Cohort id
Academy A route + Academy B Waitlist id
Academy A route + Academy B-only Teacher id
Academy A parent + Academy B-only Student id
```

All must fail safely.

Do not return another tenant's object merely to explain why access is denied.

---

# 47. Multi-Academy Teacher Scenario

Mandatory test setup:

```text
Teacher T

Academy A
  active membership
  approved = true
  max_weekly_hours = 10
  TeacherTrack = Tajweed
  availability = Monday 08:00–12:00

Academy B
  active membership
  approved = true
  max_weekly_hours = 4
  TeacherTrack = Arabic
  availability = Monday 14:00–18:00
```

Prove:

```text
A Tajweed booking during A availability -> potentially valid
A Arabic booking -> denied

B Arabic booking during B availability -> potentially valid
B Tajweed booking -> denied

A cannot use B availability
B cannot use A availability

capacity counters remain independent
```

---

# 48. Cross-Academy Double Booking Scenario

Mandatory test:

```text
Teacher T active and approved in A and B
A and B both expose overlapping availability
```

Attempt:

```text
A booking 10:00–10:30
B booking 10:00–10:30
```

Expected:

```text
first succeeds
second is rejected as overlapping
```

regardless of which academy booked first.

This proves global human-time protection survived the tenancy migration.

---

# 49. Routing Isolation Scenario

Mandatory test:

```text
Academy A
  no available lead
  no eligible sub

Academy B
  available lead
  eligible sub
```

A route request in Academy A must produce:

```text
NoCapacity
```

It must not borrow Academy B's teacher.

This is one of the core SaaS security invariants.

---

# 50. Preferred Teacher Isolation Scenario

Teacher T belongs only to Academy B.

A student in Academy A submits:

```text
preferred_teacher = T
```

Expected:

```text
rejected
```

Do not create a waitlist entry in Academy A.

Do not expose Academy B teacher configuration in the error response.

---

# 51. Parent Isolation Scenario

Mandatory:

```text
Parent P -> Student S globally

Parent P active in Academy A
Student S active only in Academy B
```

An Academy A booking request for S must fail.

Then give P active Academy B membership.

Academy B may allow the request according to the normal scheduling rules.

---

# 52. Cohort Isolation Scenario

Create:

```text
Cohort A under Level A
Cohort B under Level B
```

Prove:

```text
Academy A open-cohort endpoint returns A only
Academy B open-cohort endpoint returns B only
Academy A routing never seats a student in B
```

---

# 53. Waitlist Isolation Scenario

Create a teacher who works for both academies.

Create independent preferred-teacher requests in A and B.

Prove:

```text
A teacher waitlist endpoint under A returns A entries only
B endpoint returns B entries only

promoting A does not mutate B
fulfilling A does not fulfill B
```

---

# 54. Legacy Availability Migration

Availability is the scheduling object that lacks a derivable academy.

Design a deterministic backfill.

Follow the pattern from:

```text
curriculum/legacy.py
```

Possible resolution inputs may include:

- existing organizations;
- teacher memberships;
- migrated curriculum relationships;
- explicit environment configuration where necessary.

Do not guess when a teacher belongs to multiple academies and a legacy availability row cannot be assigned uniquely.

In ambiguous cases:

```text
refuse migration with actionable instructions
```

is better than silently choosing an academy.

---

# 55. Legacy Booking Ownership

Existing Booking ownership should normally be derivable from:

```text
booking.level.track.organization
```

because curriculum is already academy-owned after Phase 3.

Validate this assumption during the Phase 4 audit.

Do not add an organization column merely to make the migration easier.

---

# 56. Legacy Cohort Ownership

Existing Cohort ownership should derive from:

```text
cohort.level.track.organization
```

Validate all existing rows before final migration.

---

# 57. Legacy Waitlist Ownership

Existing TeacherWaitlist ownership should derive from:

```text
waitlist.level.track.organization
```

Do not duplicate or reassign entries to unrelated academies.

---

# 58. Migration Discipline

For all scheduling migrations:

- preserve primary keys where practical;
- do not invent academies;
- do not duplicate users;
- do not restore suspended memberships;
- do not assign teachers to every academy;
- do not grant every teacher every track;
- make ownership non-ambiguous;
- keep fresh-database migrations straightforward;
- unit-test nontrivial backfill decisions;
- document ambiguity or accepted compromise.

---

# 59. API Response Privacy

Scheduling responses must not leak cross-academy data through nested serializers.

Check nested:

```text
teacher
student
level
track
cohort
waitlist
```

representations.

Tenant-scoping the top-level queryset remains the primary protection.

Do not rely on nested serializer field omission as authorization.

---

# 60. OpenAPI

Update Swagger/OpenAPI so the final routes clearly require:

```text
organization_id
```

where appropriate.

Document academy-scoped scheduling endpoints.

Remove or deprecate retired global scheduling routes so the schema does not encourage clients to use the old single-academy API.

---

# 61. Tests

Add a dedicated scheduling tenancy test suite.

At minimum cover:

1. availability isolation;
2. level object-id isolation;
3. student membership;
4. parent membership;
5. teacher membership;
6. suspended membership;
7. academy-specific approval;
8. academy-specific TeacherTrack eligibility;
9. academy-specific weekly capacity;
10. cross-academy physical overlap;
11. direct booking isolation;
12. cohort isolation;
13. lead routing isolation;
14. sub routing isolation;
15. preferred-teacher isolation;
16. waitlist isolation;
17. waitlist promotion isolation;
18. API queryset isolation.

Retain all previous tests.

---

# 62. PostgreSQL Gate

Before Phase 4 completion run:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

The full suite must run against PostgreSQL.

Fresh migrations must work on an empty PostgreSQL database.

Do not mark the phase done based only on an upgraded developer database.

---

# 63. Manual Acceptance Journey

Create:

```text
Academy A
Academy B
```

Create one teacher who belongs to both.

Configure:

```text
A:
  approved
  Tajweed
  10 weekly hours
  morning availability

B:
  approved
  Arabic
  4 weekly hours
  afternoon availability
```

Create a student in A and another in B.

Walk the API:

1. list A availability;
2. confirm B availability is absent;
3. create A Tajweed booking;
4. reject A Arabic booking;
5. route A student;
6. confirm only A teachers are considered;
7. create B Arabic booking;
8. verify capacity is independent;
9. attempt cross-academy overlapping session for same teacher;
10. verify global overlap rejection;
11. try Academy A request using Academy B Level id;
12. verify rejection;
13. try Academy A request using Academy B-only student;
14. verify rejection;
15. verify waitlist routes remain isolated.

Record results before accepting the phase.

---

# 64. Documentation

At Phase 4 completion update:

```text
CLAUDE.md
learnings.md
tech-debt.md
```

Record at least:

- final Availability tenancy design;
- why Booking/Cohort/Waitlist derive organization through Level/Track;
- global TeacherBookingLock decision;
- academy-specific capacity vs global physical-overlap distinction;
- remaining `TeacherProfile` consumers;
- retired global scheduling routes;
- legacy scheduling migration decisions;
- any deferred scheduling problems.

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

# 66. Recommended Implementation Sequence

Implement in small commits.

## Task 4.1 — Audit only

Do not change code.

Search every consumer of:

```text
Availability
TeacherBookingLock
Booking
Cohort
TeacherWaitlist
TeacherProfile.approved
TeacherProfile.max_weekly_hours
TeacherProfile.specialties
OrganizationTeacherConfiguration
TeacherTrack
bookable_teacher_error
specialty_error
weekly_committed_minutes
remaining_weekly_minutes
lead_teacher
matching_sub_teachers
Level.objects.all()
ParentLink
IsLeadTeacher
IsTeacher
IsStudentOrParent
```

Report:

- files;
- current behaviour;
- proposed tenant source;
- migration risk.

Stop after the audit.

## Task 4.2 — Ownership/query layer

Introduce tenant query helpers and derived organization properties.

Do not migrate routing yet.

## Task 4.3 — Availability tenancy

Make Availability academy-specific.

Implement deterministic legacy migration and tests.

## Task 4.4 — Teacher scheduling configuration

Switch scheduling approval and weekly cap to `OrganizationTeacherConfiguration`.

## Task 4.5 — TeacherTrack migration

Switch scheduling specialty checks to `TeacherTrack`.

Do not delete `TeacherProfile.specialties` yet.

## Task 4.6 — Booking and cohort tenancy

Add student/teacher/level academy validation while preserving overlap and locking.

## Task 4.7 — Routing tenancy

Make cohort, lead, sub and preferred teacher resolution organization-aware.

## Task 4.8 — Waitlist tenancy

Scope scheduling waitlist behaviour.

Do not migrate PricingAgreement.

## Task 4.9 — API tenancy

Introduce academy-scoped scheduling routes and safely retire global tenant-assuming routes.

## Task 4.10 — Tenant isolation tests

Build two-academy adversarial tests.

## Task 4.11 — Acceptance

Run:

```text
full test suite
fresh PostgreSQL migration
check
makemigrations --check
OpenAPI verification
manual API journey
```

Then update documentation.

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