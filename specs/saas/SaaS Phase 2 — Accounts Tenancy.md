# SaaS Phase 2 — Accounts Tenancy

## Goal

Make the existing `accounts` domain compatible with the new multi-tenant organization architecture.

SaaS Phase 1 established:

```text
Organization
OrganizationMembership
```

SaaS Phase 2 establishes how existing account relationships interact with those organizations.

The goal is:

> A user remains one global account while their participation, teaching identity, and parent/student access are correctly constrained by the organizations they belong to.

This phase must not yet make curriculum, scheduling, pricing, assessment, or payouts tenant-aware.

---

# 1. Current architecture

The current accounts domain contains:

```text
User
ParentLink
TeacherProfile
```

`User` currently has the global account roles:

```text
lead
sub
student
parent
```

The organization layer now has:

```text
OrganizationMembership.role

owner
admin
staff
teacher
```

These are deliberately different concepts.

---

# 2. Core model

The desired conceptual architecture is:

```text
                    User
                     |
        +------------+------------+
        |            |            |
        v            v            v
  Organization   Organization   Organization
  Membership     Membership     Membership
        |            |            |
        v            v            v
   Academy A     Academy B     Academy C
```

A single user may participate in multiple academies.

Example:

```text
User: Ahmad

Academy A
    membership.role = teacher

Academy B
    membership.role = admin
```

The user remains one `accounts.User`.

There must not be multiple copies of the same user account merely because they belong to different academies.

---

# 3. Phase 2 responsibilities

This phase owns:

- account/organization membership compatibility;
- organization-aware account helpers;
- parent/student organization isolation;
- teacher organization membership compatibility;
- organization-specific teacher configuration;
- account API organization context;
- account tenancy regression tests.

This phase does not own:

- curriculum tenancy;
- scheduling tenancy;
- pricing tenancy;
- assessment tenancy;
- payout tenancy;
- invitations;
- academy onboarding;
- branches;
- billing;
- frontend.

---

# 4. User remains global

Do not add:

```text
organization = ForeignKey(...)
```

to `User`.

Do not duplicate `User`.

Do not make email or username organization-scoped.

The following remain global:

```text
username
email
password
first_name
last_name
timezone
date_of_birth
is_minor
signup_code
```

The user's global identity remains independent of academy membership.

---

# 5. Existing User.role remains

Keep:

```text
class Role:
    LEAD
    SUB
    STUDENT
    PARENT
```

Do not remove or rename these values.

Do not introduce:

```text
User.role = admin
User.role = teacher
User.role = staff
User.role = owner
```

Those are organization-level concepts.

---

# 6. Organization membership compatibility

Phase 1 allows:

```text
OrganizationMembership.role =
owner/admin/staff/teacher
```

without changing `User.role`.

Phase 2 now introduces account-domain validation where appropriate.

The system must be able to distinguish:

```text
account identity
+
organization authority
+
organization teaching/student/parent participation
```

Do not collapse these into one role field.

---

# 7. Organization account participation

For account-domain operations, define the concept:

```text
active account membership
```

as:

```text
OrganizationMembership.status == active
```

A suspended organization membership must not grant organization-specific account access.

Use the existing `organizations.active_membership()` helper rather than creating a second implementation.

There must be one canonical answer to:

> Is this user an active member of this organization?

---

# 8. Account organization helpers

Introduce small helpers where needed to answer questions such as:

```text
is user an active member of organization?
is parent an active member of organization?
is student an active member of organization?
is teacher an active member of organization?
```

Do not create a large service framework.

Prefer small querysets/functions that reuse `OrganizationMembership`.

Example conceptual behaviour:

```text
active_membership(user, organization)
    -> membership or None
```

For role-specific checks:

```text
active teacher membership
active student membership
active parent membership
```

should be derived from the existing membership plus the account role/profile rules.

Do not duplicate membership status logic.

---

# 9. ParentLink

Current model:

```text
ParentLink
    parent
    student
```

The relationship is currently global.

Phase 2 must ensure that an academy-specific operation cannot use a global parent-child link to cross tenant boundaries.

The central invariant is:

```text
ParentLink alone
    ≠
authorization to another organization's data
```

---

# 10. Parent/student organization rule

For organization-scoped account operations involving a parent and student:

```text
parent
    must have active membership in organization

AND

student
    must have active membership in organization
```

unless a later explicit product rule states otherwise.

Therefore:

```text
Academy A

Parent P ∈ A
Student S ∈ A

→ parent may act on the academy-scoped relationship
```

while:

```text
Academy A

Parent P ∈ A
Student S ∉ A

→ Academy A must not expose S's Academy B data
```

This is the key Phase 2 tenant-isolation rule.

---

# 11. ParentLink API

The existing parent-link endpoint is:

```text
POST /api/accounts/parent-links/
```

The current flow uses the student's signup code.

Phase 2 must determine how organization context is supplied.

The preferred direction is an explicit organization-scoped operation, for example:

```text
POST /api/organizations/{organization_id}/parent-links/
```

or another repository-consistent organization-scoped route.

However, do not create two competing parent-link APIs.

Before changing the existing endpoint:

1. inspect its existing clients/tests;
2. determine whether backwards compatibility is required;
3. decide whether the old endpoint should remain as an account-level relationship endpoint;
4. document the decision in `learnings.md`.

If a product decision is required, stop and ask.

---

# 12. ParentLink creation

When creating a parent/student relationship through an organization-scoped workflow:

1. authenticate the parent;
2. resolve the student using the existing signup code;
3. verify the student account is actually a student;
4. verify the parent account is actually a parent;
5. verify both accounts have active membership in the target organization;
6. create the relationship;
7. do not grant access to other organizations.

The signup code identifies the student.

It does not identify or authorize an organization.

---

# 13. ParentLink reads

A parent may retrieve linked children only where the request has a legitimate organization context.

Do not turn:

```text
GET /api/accounts/my-children/
```

into a global directory of every student relationship that can be used to cross tenant boundaries.

If the endpoint remains account-global, its response must contain only information appropriate to a global parent-child relationship and must not expose organization-owned data.

If academy-specific information is returned, the endpoint must become organization-scoped.

---

# 14. Cross-tenant parent test

The following test is mandatory:

```text
Organization A
Organization B

Parent P
Student S

P ∈ A
P ∈ B

S ∈ B
S ∉ A
```

Then verify:

```text
P acting inside A
    cannot access S as an A student
```

while:

```text
P acting inside B
    may access S through B
```

provided the normal parent/student relationship exists.

This test proves that global family relationships do not bypass tenancy.

---

# 15. Teacher membership

A teacher participating in an organization is represented by:

```text
OrganizationMembership
```

with:

```text
role = teacher
```

This does not automatically change:

```text
User.role
```

and does not automatically create a teacher profile.

---

# 16. Teacher account compatibility

Under the existing account model:

```text
User.role = lead
User.role = sub
```

identifies teaching accounts.

Therefore the organization teacher membership must not create an impossible combination.

For example, the system should not silently treat:

```text
User.role = parent
OrganizationMembership.role = teacher
```

as a valid teaching identity if the existing account domain does not support it.

The exact validation policy must preserve existing product behaviour.

If the correct policy is ambiguous, document the decision before implementation.

---

# 17. TeacherProfile problem

The existing model is:

```text
TeacherProfile
    user = OneToOneField(User)
```

and currently contains:

```text
bio
max_weekly_hours
hourly_payout_rate
is_lead
approved
specialties
```

A global `OneToOneField(User)` is problematic for the long-term SaaS model.

A teacher may eventually work for:

```text
Academy A
Academy B
```

with different:

```text
capacity
approval status
payout rate
teaching configuration
```

Therefore a single global profile cannot represent all organization-specific teacher state.

---

# 18. TeacherProfile migration objective

Phase 2 must establish the correct organization-specific teacher configuration without breaking existing scheduling behaviour.

The likely conceptual target is:

```text
User
   |
   +-- OrganizationMembership
           |
           +-- Teacher configuration
```

The organization-specific configuration may eventually contain:

```text
bio
max_weekly_hours
hourly_payout_rate
is_lead
approved
```

while global identity remains on `User`.

Do not blindly implement this structure without inspecting all existing consumers.

---

# 19. Existing scheduling dependency

Current scheduling depends on teacher profile information.

Before changing `TeacherProfile`, search the entire repository for:

```text
teacher_profile
TeacherProfile
max_weekly_hours
hourly_payout_rate
approved
is_lead
specialties
```

Identify every consumer.

The migration must not accidentally break:

- teacher eligibility;
- approval;
- weekly capacity;
- routing;
- payout calculation.

---

# 20. Specialties

`TeacherProfile.specialties` currently references:

```text
curriculum.Track
```

The existing architecture already has this dependency.

Do not redesign teacher specialties in Phase 2.

Do not make `Track` organization-scoped yet.

The curriculum tenancy phase must later resolve:

```text
Organization
    |
    +-- Track
          |
          +-- teacher eligibility
```

For Phase 2, preserve the existing relationship unless the teacher-profile migration technically requires a compatibility layer.

If it does, make the smallest possible change and record it.

---

# 21. Teacher profile data classification

During implementation, explicitly classify each existing teacher field.

### Global

Fields describing the person rather than the academy.

Potential example:

```text
bio
```

if product semantics confirm it is a general teacher biography.

### Organization-specific

Fields describing how the teacher operates inside one academy.

Likely:

```text
max_weekly_hours
hourly_payout_rate
approved
is_lead
```

Do not make this classification silently.

Record the decision in:

```text
learnings.md
```

---

# 22. Organization-specific approval

A teacher's approval should eventually be understood as:

```text
approved by Academy A
```

not:

```text
approved everywhere
```

Therefore:

```text
Teacher T

Academy A → approved
Academy B → not approved
```

must be representable.

Do not solve this by duplicating the `User`.

---

# 23. Organization-specific capacity

Similarly:

```text
Teacher T

Academy A → max 10 hours
Academy B → max 20 hours
```

must eventually be representable.

Phase 2 should prepare this model.

Scheduling will consume the organization-specific value in its own tenancy phase.

Do not rewrite weekly capacity logic in this phase.

---

# 24. Organization-specific payout rate

The current `TeacherProfile.hourly_payout_rate` is already used by payout logic.

In SaaS, payout rates will eventually belong to an academy-teacher relationship.

Example:

```text
Teacher T
    Academy A → $10/hour
    Academy B → $15/hour
```

Phase 2 may establish the account-side storage boundary for this value if required by the teacher-profile migration.

It must not change payout calculation.

The payout tenancy phase owns financial behaviour.

---

# 25. Lead semantics

The current system uses:

```text
User.role = lead
TeacherProfile.is_lead
```

and keeps them synchronized.

This is a legacy single-academy assumption.

Do not attempt to solve the complete lead/owner migration in one step.

For Phase 2:

- preserve existing `User.role`;
- preserve existing `TeacherProfile` compatibility;
- introduce organization-aware teaching identity;
- document the remaining migration.

A later phase may replace academy leadership with:

```text
OrganizationMembership.role = owner/admin
```

but that is not an automatic Phase 2 rewrite.

---

# 26. Account API organization context

Inspect:

```text
accounts/views.py
accounts/serializers.py
accounts/permissions.py
accounts/urls.py
```

and identify which endpoints are:

```text
global account endpoints
```

versus:

```text
organization-scoped endpoints
```

Current endpoints include:

```text
POST /api/auth/register/
GET  /api/accounts/me/
GET  /api/accounts/my-children/
POST /api/accounts/parent-links/
```

Do not blindly add organization parameters to all of them.

Classify them first.

---

# 27. Registration

Registration remains global.

A user registers one account:

```text
User
```

Registration does not automatically create membership in an arbitrary academy.

For example:

```text
POST /api/auth/register/
```

creates:

```text
User
```

not:

```text
User
+
OrganizationMembership
```

unless an explicit onboarding flow later provides organization context.

This prevents a public signup from silently entering the wrong tenant.

---

# 28. `/accounts/me/`

The current `/api/accounts/me/` endpoint is a global identity endpoint.

It may continue returning:

```text
username
email
name
role
timezone
date_of_birth
is_minor
```

and other global account information.

If organization memberships are included, they must come from the organization system and must not duplicate organization authorization logic.

Do not make `/me/` return the complete private data of every organization the user belongs to.

---

# 29. Organization-specific account views

When an organization-specific account endpoint is introduced, it must follow:

```text
URL organization
        |
        v
active membership
        |
        v
organization-scoped queryset
```

Never:

```text
request.user
    ↓
all related objects
```

without organization filtering.

---

# 30. Permissions

Reuse the organization permission system created in SaaS Phase 1.

Do not create a second:

```text
OrganizationMembershipPermission
```

implementation.

Use the existing:

```text
active_membership()
IsOrganizationMember
CanManageOrganizationMemberships
OwnerMembershipIsProtected
```

where appropriate.

Add new account-specific permissions only when they represent a genuinely new rule.

---

# 31. Suspended memberships

If:

```text
OrganizationMembership.status = suspended
```

then the user must not use organization-specific account operations for that organization.

The account itself remains active.

For example:

```text
User
  |
  +-- Academy A → active
  +-- Academy B → suspended
```

means:

```text
User can operate in A.

User cannot operate in B.
```

This must not disable the entire Django account.

---

# 32. Multiple organizations

Mandatory test:

```text
User T

Academy A → teacher
Academy B → teacher
```

The same user must be able to exist in both organizations.

The implementation must not overwrite one organization's configuration when updating the other.

This is one of the primary reasons the account tenancy migration exists.

---

# 33. Security invariants

Phase 2 must enforce:

### Invariant 1

A global user account does not imply access to every academy.

### Invariant 2

A parent-child relationship does not bypass organization membership.

### Invariant 3

A teacher membership does not automatically grant access to another academy.

### Invariant 4

A suspended membership grants no normal organization-specific account access.

### Invariant 5

Organization-specific teacher configuration cannot be read or modified through another organization.

### Invariant 6

Client-supplied organization IDs never override server-side membership checks.

---

# 34. Tests

## User membership tests

Test:

```text
User A → Academy A
User A → Academy B
```

and verify both memberships remain independent.

---

## Parent/student tests

Test:

```text
Parent P
Student S

P ∈ A
S ∈ A
```

works.

Then:

```text
P ∈ A
S ∉ A
```

cannot expose S's organization-specific data through A.

---

## Teacher tests

Test:

```text
Teacher T ∈ A
Teacher T ∈ B
```

with independent organization-specific teacher configuration.

Changing:

```text
T in A
```

must not change:

```text
T in B
```

---

## Role separation tests

Test:

```text
User.role = student
OrganizationMembership.role = owner
```

does not mutate:

```text
User.role
```

Also verify that:

```text
OrganizationMembership.role = teacher
```

does not silently mutate:

```text
User.role
```

unless the explicitly approved design requires it.

---

## Suspended membership tests

Test:

```text
active membership
    → organization account access allowed

suspended membership
    → organization account access denied
```

---

# 35. Regression requirements

The following existing behaviours must remain intact:

### Registration

- public registration still works;
- password validation remains;
- role choices remain;
- minor calculation remains;
- signup code generation remains.

### Parent links

- valid parent/student roles remain enforced;
- duplicate links remain prevented;
- self-links remain prevented;
- invalid signup codes remain safely handled.

### Teacher profiles

- teacher-role restrictions remain enforced;
- `is_lead` compatibility remains enforced unless the phase explicitly changes it.

### Authentication

- login remains unchanged;
- token authentication remains unchanged.

Do not change authentication architecture during Phase 2.

---

# 36. Database migration safety

If existing models are changed:

1. inspect existing production-like development data;
2. identify rows affected;
3. design the migration before editing the model;
4. avoid destructive migrations;
5. preserve existing account relationships;
6. run migration checks on a fresh database;
7. run relevant regression tests.

Do not delete the existing `TeacherProfile` or `ParentLink` data merely to make the new model easier.

---

# 37. Suggested implementation tasks

Do not implement this phase as one giant Claude Code request.

---

## Task 2.1 — Account tenancy audit

Before modifying models:

Inspect:

```text
accounts/models.py
accounts/serializers.py
accounts/views.py
accounts/permissions.py
accounts/urls.py
```

Search the repository for:

```text
User.role
teacher_profile
TeacherProfile
ParentLink
parent_links
child_links
max_weekly_hours
hourly_payout_rate
approved
is_lead
specialties
signup_code
```

Produce a dependency map.

Do not modify code.

Output:

```text
model/field
current meaning
current consumers
global or organization-specific
proposed Phase 2 treatment
later-phase dependency
```

Stop.

---

## Task 2.2 — Organization-aware account helpers

Introduce only the smallest helpers required to answer:

```text
active member of organization
active teacher in organization
active student in organization
active parent in organization
```

Reuse the organization foundation.

Add focused tests.

Do not modify curriculum or scheduling.

---

## Task 2.3 — Parent/student organization isolation

Make organization-scoped parent/student operations enforce:

```text
parent active in organization
AND
student active in organization
```

Preserve the global `ParentLink` unless the dependency audit proves an organization field is necessary.

Do not create duplicate parent-child models without a concrete requirement.

Add cross-tenant tests.

---

## Task 2.4 — TeacherProfile tenancy design

This is the most important task.

Before changing `TeacherProfile`, produce a short design decision based on actual repository consumers.

Determine:

```text
Which fields are global?
Which fields are organization-specific?
How can one teacher belong to multiple organizations?
How will existing scheduling references remain compatible?
How will existing payout references remain compatible?
```

Do not implement the migration until the design is explicit.

Record the decision in `learnings.md`.

---

## Task 2.5 — Implement teacher organization configuration

Implement the approved model from Task 2.4.

Requirements:

- one teacher can belong to multiple organizations;
- organization-specific configuration is independent;
- teacher identity remains one `User`;
- existing profile invariants remain enforced;
- no cross-tenant configuration leakage;
- no curriculum tenancy yet;
- no scheduling rewrite;
- no payout calculation rewrite.

Add focused tests.

---

## Task 2.6 — Organization-aware account API

Update only account endpoints that genuinely require organization context.

Preserve global endpoints such as registration where appropriate.

Add:

```text
organization membership validation
organization-scoped querysets
organization-aware permissions
```

where required.

Document any API compatibility changes.

---

## Task 2.7 — Cross-tenant regression suite

Create a focused suite covering:

```text
multiple organizations
parent/student isolation
teacher isolation
suspended memberships
role separation
organization-specific teacher configuration
```

Do not create broad duplicate tests.

---

# 38. Manual verification

Use `/api/docs/`.

At minimum verify:

```text
1. Create Academy A.
2. Create Academy B.

3. Create a teacher account.

4. Add the teacher to Academy A.

5. Add the same teacher to Academy B.

6. Verify both memberships exist.

7. Configure teacher differently in A and B.

8. Verify A configuration does not appear in B.

9. Create a parent.

10. Create a student.

11. Establish the parent/student relationship.

12. Add both to Academy A.

13. Verify the parent can operate on the relationship inside A.

14. Remove/suspend the student's A membership.

15. Verify the parent cannot use A to access the student's A-specific information.

16. Suspend the parent membership in A.

17. Verify organization-specific parent operations fail.

18. Verify the user's global account remains usable elsewhere.
```

---

# 39. Acceptance criteria

Phase 2 is complete when:

### Identity

1. `User` remains a global identity.

2. `User` is not given a single `organization` foreign key.

3. Existing global account roles remain valid.

### Membership

4. A user can belong to multiple organizations.

5. Organization membership is the basis for organization-specific account access.

6. Suspended membership does not grant organization-specific access.

### Parent/student

7. Parent/student organization operations require both accounts to have appropriate active membership.

8. A parent-child relationship cannot be used to cross organization boundaries.

9. Existing parent/student validation remains intact.

10. Existing signup-code behaviour remains intact.

### Teacher

11. A teacher can belong to multiple organizations.

12. Organization-specific teacher configuration is independently stored.

13. Changing teacher configuration for Academy A does not change Academy B.

14. Teacher account identity remains global.

15. Existing teacher validation remains intact.

16. Teacher specialties remain compatible with the current curriculum model until curriculum tenancy is implemented.

### API

17. Organization-specific account endpoints verify active organization membership.

18. Client-supplied organization identifiers cannot bypass membership checks.

19. Existing global account endpoints continue working.

20. Authentication behaviour remains unchanged.

### Security

21. Cross-tenant parent/student access is tested.

22. Cross-tenant teacher access is tested.

23. Cross-tenant teacher configuration access is tested.

24. Suspended membership access is tested.

25. Organization roles and global account roles remain separate.

### Regression

26. Existing account tests pass.

27. Existing curriculum tests pass.

28. Existing scheduling tests pass.

29. Existing pricing tests pass.

30. Existing assessment tests pass.

31. Existing payout tests pass.

### Quality

32. `python manage.py check` passes.

33. `python manage.py makemigrations --check` passes.

34. Fresh-database migrations succeed.

35. Manual account journeys are verified.

36. Important architecture decisions are recorded in `learnings.md`.

37. Deliberate remaining limitations are recorded in `tech-debt.md`.

---

# 40. Explicitly out of scope

Do not implement:

- curriculum organization ownership;
- organization-scoped Track;
- organization-scoped Level;
- organization-scoped PlacementResult;
- organization-scoped Availability;
- organization-scoped Booking;
- organization-scoped Cohort;
- organization-scoped routing;
- organization-scoped PricingAgreement;
- organization-scoped Assessment;
- organization-scoped Payout;
- organization branches;
- academy invitations;
- invitation tokens;
- email invitations;
- student bulk import;
- teacher bulk import;
- CSV import;
- academy billing;
- subscriptions;
- WhatsApp integration;
- Telegram integration;
- Google Meet integration;
- Jitsi integration;
- notifications;
- frontend;
- user-role redesign;
- complete owner/lead migration;
- organization deletion;
- ownership transfer.

---

# 41. Architectural decisions to record

During this phase, `learnings.md` should record at minimum:

```text
1. User is a global identity and is not directly assigned to one organization.

2. OrganizationMembership represents the user's relationship with an academy.

3. User.role remains separate from OrganizationMembership.role.

4. ParentLink is a global family relationship and cannot by itself authorize
   access to organization-owned student data.

5. Teacher-specific operational configuration must be organization-aware
   when the same teacher can work for multiple academies.

6. Curriculum-linked teacher specialties remain temporarily coupled to the
   existing curriculum model until curriculum tenancy is implemented.
```

Only record decisions actually made during implementation.

Do not record hypothetical decisions as completed architecture.

---

# 42. Technical debt

Potential deferred issues should be recorded explicitly rather than solved opportunistically.

Examples:

```text
legacy User.role versus organization membership role;
legacy TeacherProfile compatibility;
teacher specialties coupled to curriculum;
organization onboarding;
ownership versus lead-teacher semantics.
```

Do not mark these as bugs if they are deliberate migration boundaries.

---

# 43. Final architectural state

At the end of Phase 2, the system should conceptually support:

```text
                         User
                          |
             +------------+------------+
             |                         |
             v                         v
      Academy A                   Academy B
             |                         |
      Membership                  Membership
        teacher                     teacher
             |                         |
      Teacher Config A          Teacher Config B
```

and:

```text
Parent
  |
  +-- Student
```

may remain a global family relationship, but organization-specific access must still satisfy:

```text
Parent ∈ Organization
        AND
Student ∈ Organization
```

The result is a clean bridge between the original account system and the future tenant-scoped business domains.

The next phase can then safely move into:

```text
Organization
      |
      +-- Curriculum
```

without having to solve basic identity/membership semantics at the same time.

---

# 44. Stop condition

Do not begin SaaS Phase 3 until:

```text
User
  |
  +-- Academy A
  |
  +-- Academy B
```

works correctly,

and:

```text
Parent/Student
Teacher
Teacher configuration
```

cannot leak across those organizations.

The goal is not to finish the entire SaaS platform.

The goal is to make the **accounts boundary trustworthy enough that curriculum tenancy can be built on top of it safely**.