# SaaS Phase 1 — Organization Foundation

## Goal

Introduce the first architectural layer required to transform the existing single-academy Quran platform into a multi-tenant SaaS product.

This phase creates the `Organization` tenant and the relationship between users and organizations through `OrganizationMembership`.

It does **not** yet make curriculum, scheduling, pricing, assessment, payouts, or other existing domains tenant-aware.

The phase exists to establish a clean tenancy foundation before existing business domains are migrated one by one.

---

## Why this phase now

The existing application was originally designed around one academy.

The current backend already contains substantial working domains:

- accounts;
- curriculum;
- scheduling;
- routing;
- pricing;
- waitlists;
- assessment;
- progress;
- payouts;
- production storage/security.

Those existing domains must now eventually support multiple independent academies.

The safest migration is incremental.

Before adding organization ownership to existing domain models, the platform needs a stable definition of:

```text
Organization
    +
Organization Membership
```

This phase introduces exactly those concepts and proves the first tenant-access boundary.

It does not redesign any completed product phase.

---

# 1. Architectural model

The new platform hierarchy begins as:

```text
User
  |
  +----------------------+
  |                      |
  v                      v
Organization A       Organization B
  |                      |
  v                      v
Membership            Membership
```

The critical distinction is:

```text
User
    =
global identity

OrganizationMembership
    =
relationship between a user and one academy
```

Do not use the existing `User.role` as the organization's authorization role.

The existing application currently uses:

```text
lead
sub
student
parent
```

for user identity/business behaviour.

That model remains unchanged in this phase.

The new organization-level role answers a different question:

> What authority does this user have inside this academy?

---

# 2. New Django app

Create:

```text
organizations/
├── __init__.py
├── admin.py
├── apps.py
├── migrations/
├── models.py
├── permissions.py
├── serializers.py
├── tests/
│   ├── __init__.py
│   ├── factories.py
│   ├── test_api.py
│   └── test_models.py
├── urls.py
└── views.py
```

A `services.py` file is optional.

Do not add one merely for convenience.

Use the repository's existing app structure and testing patterns.

---

# 3. Organization model

## `Organization`

Fields:

```text
id
name
slug
timezone
is_active
created_at
updated_at
```

### `name`

Human-readable institution name.

Examples:

```text
Al-Huda Quran Academy
Noor Learning Centre
Taqwa Online Institute
```

The name is not globally unique.

Two organizations may legitimately have similar names.

### `slug`

Unique machine-readable identifier.

Example:

```text
al-huda-quran-academy
```

Slug uniqueness is **global in this phase**.

Do not design organization slugs as a tenant-scoped concept because an organization is itself the tenant.

### `timezone`

IANA timezone string.

Example:

```text
Africa/Lagos
```

Use the same timezone validation convention already used by `accounts.User`.

Do not create a second incompatible timezone validator.

### `is_active`

Boolean indicating whether the organization is active.

Default:

```text
True
```

Inactive organizations should not be deleted merely to disable access.

Deletion policy is out of scope.

### timestamps

Use repository conventions for creation/update timestamps.

---

# 4. Organization membership model

## `OrganizationMembership`

Fields:

```text
id
organization
user
role
status
created_at
updated_at
```

### `organization`

Foreign key to `Organization`.

Use a deletion policy appropriate for preserving membership history.

Preferred initial behaviour:

```text
Organization deletion cascades membership
```

because this phase is not introducing historical financial or teaching records into the organization model itself.

The final organization deletion strategy can be revisited before production multi-tenant deletion workflows exist.

### `user`

Foreign key to `accounts.User`.

A user may belong to multiple organizations.

Do not put an `organization` foreign key directly on `User`.

That would incorrectly force:

```text
one user = one academy
```

which is not a safe SaaS assumption.

### `role`

Organization-level role.

Use:

```text
owner
admin
staff
teacher
```

### `status`

Membership lifecycle.

Use the smallest lifecycle necessary for this phase:

```text
active
suspended
```

Default:

```text
active
```

Do not introduce invitations or pending-membership states yet.

Invitation workflows are a later phase because they require onboarding, email/notification, and token decisions.

### timestamps

Use repository conventions.

---

# 5. Membership uniqueness

A user must not have multiple active membership records for the same organization.

The simplest invariant for this phase is:

```text
organization + user = unique
```

This prevents duplicate membership rows entirely.

Do not create multiple historical membership rows yet.

Membership history, invitations, reactivation, and archived memberships are future design work.

---

# 6. Organization roles

## Owner

The organization creator.

Permissions:

- read organization;
- manage organization membership;
- manage organization-level settings later;
- remain the highest organization authority in this phase.

There must be exactly one initial owner created by organization creation.

No public API may create a second owner during Phase 1.

## Admin

Organization administrator.

For Phase 1:

- read organization;
- read organization memberships;
- create membership for users;
- manage membership role/status subject to the phase rules.

Do not give admins permission to transfer organization ownership yet.

Ownership transfer is a separate business operation and requires an explicit future decision.

## Staff

Operational organization member.

For Phase 1:

- read the organization they belong to;
- read their own membership.

No membership-management privileges.

## Teacher

Organization member whose existing global `User.role` may represent a lead/sub teacher.

Important:

```text
OrganizationMembership.role == teacher
```

does not replace:

```text
User.role == lead/sub
```

during this phase.

The later accounts-tenancy phase will determine how these two concepts converge.

---

# 7. Organization creation

Endpoint:

```text
POST /api/organizations/
```

An authenticated user may create an organization.

Request:

```json
{
  "name": "Al-Huda Quran Academy",
  "slug": "al-huda-quran-academy",
  "timezone": "Africa/Lagos"
}
```

The service/API must perform the following atomically:

```text
1. create Organization
2. create OrganizationMembership
       user = request.user
       role = owner
       status = active
3. commit transaction
```

There must never be a successfully created organization without its owner membership.

Use `transaction.atomic()`.

Do not rely on the caller to create the owner membership separately.

---

# 8. Organization creation rules

The authenticated creator becomes:

```text
role = owner
status = active
```

The creator is not automatically:

```text
lead teacher
sub teacher
admin
teacher
```

because organization authority and existing application roles are separate concepts.

Do not mutate `request.user.role` during organization creation.

For example:

```text
existing User.role = student

creates Organization

result:
User.role = student
OrganizationMembership.role = owner
```

This is intentional in Phase 1.

The eventual product may choose a clearer onboarding rule, but it should be a future decision.

---

# 9. Organization read access

Endpoint:

```text
GET /api/organizations/mine/
```

Returns organizations the authenticated user belongs to.

Expected behaviour:

```text
User A
 ├── Academy A
 └── Academy B

GET /mine/

→ Academy A
→ Academy B
```

A user outside an organization must never see it.

---

# 10. Organization detail access

Endpoint:

```text
GET /api/organizations/{id}/
```

A user may retrieve an organization only if they have an active membership in it.

A suspended member must not have normal organization access.

A non-member must receive an authorization failure.

Do not allow:

```text
GET /organizations/123/
```

to reveal the organization's private details simply because the ID is known.

---

# 11. Membership management

Endpoint:

```text
POST /api/organizations/{id}/memberships/
```

A membership manager may create a membership for an existing `User`.

The initial Phase 1 membership manager roles are:

```text
owner
admin
```

The creator supplies:

```json
{
  "user": 42,
  "role": "teacher"
}
```

or another supported organization-level role.

Do not accept arbitrary role values.

Do not create a new user through the membership endpoint.

User onboarding remains the responsibility of the accounts system until a later SaaS onboarding phase.

---

# 12. Membership management rules

## Owner

May:

- list memberships;
- create memberships;
- suspend memberships;
- reactivate memberships;
- change non-owner membership roles.

May not use a normal membership-management endpoint to create another owner.

## Admin

May:

- list memberships;
- create non-owner memberships;
- suspend/reactivate non-owner memberships;
- change non-owner membership roles.

An admin must not:

- create an owner;
- modify the organization's owner;
- transfer ownership.

## Staff

May not manage memberships.

## Teacher

May not manage memberships.

## Suspended members

A suspended member cannot use ordinary organization APIs as a member.

They remain in the database for the membership record.

Do not delete the membership automatically.

---

# 13. Membership listing

Endpoint:

```text
GET /api/organizations/{id}/memberships/
```

Only active members with sufficient organization authority may retrieve the organization's membership list.

At minimum:

```text
owner
admin
```

can list memberships.

A normal staff/teacher member should not receive the complete membership directory in Phase 1 unless explicitly required by a later product decision.

This deliberately minimizes exposure while the organization security model is still being introduced.

---

# 14. Own membership

The API should expose enough information for the frontend to determine the logged-in user's organization memberships.

Preferred response shape:

```text
GET /api/organizations/mine/
```

Each entry should include enough information to identify:

```text
organization
membership id
organization role
membership status
```

Do not expose another user's unrelated membership records.

---

# 15. Tenant boundary

Phase 1 introduces the following rule:

```text
organization access
=
active OrganizationMembership
```

No existing domain is yet organization-scoped.

Therefore the organization boundary is intentionally isolated at first.

Later phases will extend the same boundary into:

```text
accounts
curriculum
scheduling
pricing
assessment
payouts
```

The migration must eventually result in:

```text
Organization A
  ├── Students
  ├── Teachers
  ├── Curriculum
  ├── Bookings
  ├── Assessments
  └── Payouts

Organization B
  ├── Students
  ├── Teachers
  ├── Curriculum
  ├── Bookings
  ├── Assessments
  └── Payouts
```

But Phase 1 does not implement that yet.

---

# 16. API surface

Add:

```text
POST /api/organizations/
GET  /api/organizations/mine/
GET  /api/organizations/{id}/
POST /api/organizations/{id}/memberships/
GET  /api/organizations/{id}/memberships/
```

Exact URL naming may follow existing repository conventions if they are clearer, but the permission model must remain unchanged.

---

# 17. Permissions

Create organization-aware permission classes rather than embedding the same role checks into every view.

Suggested concepts:

```text
IsOrganizationMember
IsOrganizationAdmin
IsOrganizationOwner
CanManageOrganizationMemberships
```

Do not build a giant permission framework.

Keep them small and composable.

The permission class should establish:

```text
request.user
    +
target organization
    +
active membership
```

before allowing the operation.

Do not trust:

```text
organization_id
role
user_id
```

submitted by the client.

The server determines the caller's actual membership and organization role.

---

# 18. Serializer rules

The serializer must:

- validate organization fields;
- validate IANA timezone;
- validate organization-level roles;
- reject duplicate memberships;
- prevent client-supplied owner membership creation;
- prevent a caller from assigning organization roles they do not have authority to assign.

Do not rely only on serializer validation for authorization.

Permission classes and model/service validation must protect the same business boundary.

---

# 19. Model invariants

The domain/model layer should protect at minimum:

### Organization

- slug is unique;
- timezone is valid;
- name cannot be empty.

### OrganizationMembership

- organization and user must exist;
- organization/user combination is unique;
- role is valid;
- status is valid.

For owner-specific business rules that cannot be expressed with a simple database constraint, enforce them in the service/model layer.

---

# 20. Atomic organization creation

Use a service or transaction boundary so this operation is all-or-nothing:

```text
BEGIN

create Organization

create owner OrganizationMembership

COMMIT
```

On membership creation failure:

```text
ROLLBACK Organization
```

A partially-created tenant is not acceptable.

---

# 21. Existing accounts compatibility

Do not modify these existing fields during this phase:

```text
User.role
User.timezone
User.date_of_birth
User.is_minor
User.signup_code
TeacherProfile.is_lead
TeacherProfile.approved
TeacherProfile.specialties
```

The current accounts domain already has explicit validation around these fields. Preserve it.

The new organization system must coexist with that model until SaaS Phase 3 addresses account tenancy.

---

# 22. Existing domain compatibility

Do not modify:

```text
curriculum/
scheduling/
pricing/
assessment/
payouts/
```

except for required registration/import/configuration needed to make the new `organizations` app available.

Do not add organization foreign keys to those domains.

Do not rewrite:

```text
route_session()
Booking.clean()
TeacherBookingLock
PricingAgreement
SessionAssessment
TeacherPayout
```

during this phase.

---

# 23. Admin

Register:

```text
Organization
OrganizationMembership
```

in Django admin.

The admin should make it easy for the developer to inspect:

```text
organization
memberships
member roles
member status
```

Do not create a complicated custom admin dashboard.

Use normal Django admin patterns already present in the repository.

---

# 24. Tests

Create factories following the repository's existing factory style.

At minimum create factories for:

```text
Organization
OrganizationMembership
```

Use existing account factories where appropriate.

---

## 24.1 Model tests

Test:

1. Organization creation works.

2. Organization slug uniqueness is enforced.

3. Invalid timezone is rejected.

4. Membership creation works.

5. Duplicate organization/user membership is rejected.

6. Invalid membership role is rejected.

7. Invalid membership status is rejected.

8. A user may belong to multiple organizations.

Example:

```text
User
 ├── Organization A
 └── Organization B
```

must be valid.

---

## 24.2 Organization creation API tests

Test:

1. Unauthenticated user cannot create an organization.

2. Authenticated user can create an organization.

3. Creator automatically receives owner membership.

4. Owner membership is active.

5. User.role remains unchanged after organization creation.

6. Organization creation is atomic.

7. Failed owner-membership creation does not leave a partially-created organization.

---

## 24.3 Organization access tests

Create:

```text
Organization A
Organization B

User A -> member of A
User B -> member of B
```

Test:

```text
User A can read A.
User A cannot read B.
User B can read B.
User B cannot read A.
```

This cross-tenant test is mandatory.

---

## 24.4 Membership management tests

Test:

```text
Owner A can create a teacher membership in A.
Admin A can create a teacher membership in A.
Staff A cannot create memberships.
Teacher A cannot create memberships.
User B cannot create membership in A.
```

Also test:

```text
Admin A cannot create owner membership.
```

and:

```text
Admin A cannot modify Organization B.
```

---

## 24.5 Suspended membership tests

Test:

```text
active member
    -> can access organization

suspended member
    -> cannot access ordinary organization endpoints
```

The membership row must remain.

---

# 25. Acceptance criteria

The phase is complete only when all of the following are true.

### Data model

1. `organizations` Django app exists.

2. `Organization` exists with:
   - name;
   - slug;
   - timezone;
   - is_active;
   - timestamps.

3. `OrganizationMembership` exists with:
   - organization;
   - user;
   - role;
   - status;
   - timestamps.

4. Organization/user membership is unique.

5. Organization timezone uses the existing IANA timezone validation convention.

6. A user can belong to multiple organizations.

### Creation

7. An authenticated user can create an organization.

8. The organization creator automatically receives an active owner membership.

9. Organization creation and owner membership creation are atomic.

10. `User.role` is not mutated by organization creation.

### Access

11. A member can read organizations they belong to.

12. A non-member cannot read the organization.

13. A suspended member cannot use ordinary organization member access.

14. `/mine/` only returns organizations for the authenticated user.

### Membership management

15. Owner can manage memberships.

16. Admin can manage non-owner memberships.

17. Staff cannot manage memberships.

18. Teacher cannot manage memberships.

19. Admin cannot create or assign owner membership.

20. Normal membership-management APIs cannot transfer ownership.

21. Users cannot manage another organization's memberships.

### Security

22. Cross-tenant organization access is explicitly covered by automated tests.

23. Cross-tenant membership management is explicitly covered by automated tests.

24. Server-side permissions, not just serializer logic, enforce organization access.

### Repository safety

25. Existing accounts behaviour remains unchanged.

26. Existing curriculum behaviour remains unchanged.

27. Existing scheduling behaviour remains unchanged.

28. Existing pricing behaviour remains unchanged.

29. Existing assessment behaviour remains unchanged.

30. Existing payout behaviour remains unchanged.

31. No existing domain receives an `organization` foreign key in this phase.

### Quality gates

32. `python manage.py check` passes.

33. `python manage.py makemigrations --check` passes.

34. Organization model tests pass.

35. Organization API tests pass.

36. Existing regression tests pass.

37. The developer manually verifies organization creation and cross-tenant access through `/api/docs/`.

---

# 26. Explicitly out of scope

Do not implement:

- organization fields on existing domain models;
- academy branches;
- multi-branch organizations;
- organization billing;
- subscriptions;
- organization invitations;
- invitation emails;
- WhatsApp integration;
- Telegram integration;
- Google Meet integration;
- Jitsi abstraction;
- notification system;
- academy branding;
- academy settings beyond the initial organization timezone;
- frontend;
- organization analytics;
- tenant-specific pricing plans;
- payment processing;
- organization deletion workflows;
- owner transfer;
- audit log system;
- user-role migration;
- student/parent/teacher onboarding redesign.

These belong to later phases.

---

# 27. Suggested implementation breakdown

Do not implement the whole phase in one AI session.

Break it into small tasks.

## 1.1 Create the organizations app

Create the Django app and register it.

Verify:

```bash
python manage.py check
```

Commit.

Suggested commit:

```text
feat: add organizations app
```

## 1.2 Add Organization model

Implement:

```text
Organization
```

with validation, admin, factory, and model tests.

Run:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py check
python manage.py test organizations.tests.test_models
```

Commit.

Suggested commit:

```text
feat: add organization model
```

## 1.3 Add OrganizationMembership

Implement:

```text
OrganizationMembership
```

with:

- roles;
- status;
- uniqueness;
- validation;
- admin;
- factory;
- model tests.

Commit.

Suggested commit:

```text
feat: add organization memberships
```

## 1.4 Add organization creation API

Implement:

```text
POST /api/organizations/
```

with atomic owner creation.

Add focused API tests.

Commit.

Suggested commit:

```text
feat: add organization creation api
```

## 1.5 Add organization membership APIs

Implement:

```text
GET /api/organizations/mine/
GET /api/organizations/{id}/
POST /api/organizations/{id}/memberships/
GET /api/organizations/{id}/memberships/
```

Add permission tests.

Commit.

Suggested commit:

```text
feat: add organization membership api
```

## 1.6 Cross-tenant security tests

Create explicit:

```text
Organization A
Organization B
User A
User B
```

and prove isolation.

This task is about security testing, not new functionality.

Commit.

Suggested commit:

```text
test: verify organization tenant isolation
```

---

# 28. Manual verification

After implementation, manually verify:

```text
1. Register/login User A.

2. POST /api/organizations/
   -> Organization A created.
   -> User A becomes owner.

3. GET /api/organizations/mine/
   -> Organization A appears.

4. Create User B.

5. Confirm User B cannot read Organization A.

6. As User A:
   create a teacher/admin/staff membership.

7. Confirm the new membership appears.

8. Confirm a normal staff/teacher cannot manage memberships.

9. Confirm an admin cannot create another owner.

10. Confirm a suspended member loses normal organization access.
```

---

# 29. Learnings and tech debt

Record important architectural decisions in `learnings.md`.

At minimum document:

```text
- Organization is the tenant boundary.
- OrganizationMembership is separate from User.role.
- User.role remains unchanged during SaaS Phase 1.
- Organization ownership is represented by membership rather than a second owner field.
- User may belong to multiple organizations.
- Membership is unique per organization/user in Phase 1.
```

Add to `tech-debt.md` only when a real shortcut is deliberately taken.

Do not create tech-debt entries for behaviour that is simply out of scope.

---

# 30. Definition of done

SaaS Phase 1 is done when:

```text
User
  |
  +-- Organization A
  |      |
  |      +-- owner
  |
  +-- Organization B
         |
         +-- teacher
```

can be represented safely in the database,

and the API can enforce:

```text
A sees A
B sees B
A cannot see B
B cannot see A
```

without changing the behaviour of the existing Quran Academy product.

The organization layer must be stable enough for the next SaaS phase to begin tenant-scoping the accounts domain.

Do not begin curriculum, scheduling, pricing, assessment, or payout tenancy until this phase's acceptance criteria and security tests pass.

---

# Stop and ask instead of guessing

Stop before implementation when:

- the organization ownership model needs to change;
- more than one owner is required;
- organization invitations are required;
- organization membership history is required;
- a user must be prevented from belonging to multiple organizations;
- `User.role` must be changed;
- an existing domain must become organization-scoped before the next planned phase;
- a new organization-level permission is required but not defined;
- an existing security boundary would need to become less restrictive;
- organization deletion or ownership transfer becomes necessary.

Do not silently invent those behaviours.