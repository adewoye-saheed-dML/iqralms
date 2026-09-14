# QURAN ACADEMY — BACKEND BRIDGE PHASE
## Student Management & Enrollment API Enablement

**Repository:** `adewoye-saheed-dML/quran_acad`  
**Branch:** `main`  
**Phase:** SaaS Phase 13 — Student Management API Enablement  
**Purpose:** Provide the backend contract required to unblock Frontend Phase 16  
**Status:** READY TO IMPLEMENT

---

# 1. Why This Phase Exists

The frontend reached the correct boundary during Student Management & Enrollment:

```text
/app/students
```

exists, but the backend contract does not currently expose academy-admin student list/detail/create/enrollment operations.

The current backend does already have:

- a global `User` model with `role="student"`
- global `ParentLink`
- academy `Organization`
- academy `OrganizationMembership`
- tenant-aware membership access
- organization-scoped routing conventions
- organization-scoped teacher configuration

The problem is that **student identity and student academy enrollment are not yet represented as a dedicated academy-scoped resource**.

The safe solution is therefore:

```text
Global User
    +
Academy-scoped Student Enrollment
    ↓
Student Management API
```

Do **not** retrofit students into the existing organization membership role system by adding a new membership role unless a later architecture review explicitly approves that change.

---

# 2. Existing Backend Invariants

The implementation must preserve these existing invariants.

## 2.1 User is global

The existing backend explicitly treats `User` as a global identity.

Do not add:

```python
organization = ForeignKey(...)
```

to `User`.

A user may belong to multiple academies.

## 2.2 User.role remains the person type

The current `accounts.Role` includes:

```text
lead
sub
student
parent
```

`User.role` answers what the person is in the teaching domain.

Do not change these values just to support academy enrollment.

## 2.3 OrganizationMembership remains academy authority

The existing organization system distinguishes:

```text
owner
admin
staff
teacher
```

from `User.role`.

Do not make:

```text
student
```

an `OrganizationRole` in this phase.

A student's academy relationship is an **enrollment/participation record**, not an administrative membership.

## 2.4 Tenant access remains centralized

Continue using the existing:

```python
active_membership(user=..., organization=...)
```

and existing organization-scoped permission/mixin conventions.

Do not create a second tenant resolver.

---

# 3. Proposed Domain Model

Add a new academy-scoped model.

Recommended conceptual name:

```python
OrganizationStudent
```

or:

```python
StudentEnrollment
```

Use the repository naming convention that best matches the existing application vocabulary.

The record must represent:

```text
academy
student user
enrollment status
created_at
updated_at
```

Possible initial status:

```text
active
inactive
```

Only include additional states if a real product requirement and backend workflow justify them.

Do not introduce:

```text
pending
invited
waitlisted
graduated
withdrawn
```

without a defined workflow.

---

# 4. Recommended Relationship

Conceptually:

```text
Organization
    │
    └── StudentEnrollment
            │
            └── User(role=student)
```

The same student user may have multiple enrollment records across academies:

```text
Student A
  ├── Academy X enrollment
  └── Academy Y enrollment
```

This preserves the platform's multi-tenant identity model.

Do not add an academy foreign key to `User`.

---

# 5. Database Constraints

The new model should enforce at database level:

```text
unique(organization, student)
```

A student should not have two simultaneous enrollment records for the same academy unless the future design explicitly introduces enrollment history.

Prefer:

```text
one row + status change
```

over duplicate rows.

The organization must cascade/delete according to the repository's existing tenant conventions. Review the organization's current lifecycle before choosing `CASCADE` or another policy.

---

# 6. Student Eligibility

At minimum:

```python
student.role == Role.STUDENT
```

must be enforced.

Do not permit:

```text
parent
teacher
admin
lead
sub
```

users to become student enrollment records accidentally.

This validation belongs in model/service/API layers rather than only the frontend.

---

# 7. Student API Contract

Implement a new organization-scoped API following the repository convention:

```text
/api/organizations/<organization_pk>/students/
```

Recommended endpoints:

```text
GET   /api/organizations/<organization_pk>/students/
POST  /api/organizations/<organization_pk>/students/
```

and:

```text
GET   /api/organizations/<organization_pk>/students/<id>/
PATCH /api/organizations/<organization_pk>/students/<id>/
```

Add further action endpoints only when the workflow requires them.

Do not introduce a generic catch-all view.

---

# 8. GET Student List

Required behavior:

```text
authenticated user
→ verified academy membership
→ role permission
→ academy-scoped student queryset
```

The queryset must only return students enrolled in the requested academy.

A user knowing another academy's student ID must not be able to retrieve that student.

The list response should expose only fields required by the frontend.

Recommended identity fields:

```text
id
user_id
username
email
first_name
last_name
date_of_birth
is_minor
enrollment_status
created_at
updated_at
```

Only expose date-of-birth/minor information if product privacy rules permit it.

Do not expose:

```text
password
signup_code
authentication tokens
private internal fields
```

unless explicitly required by an existing parent/student workflow.

---

# 9. List Filtering

Start only with filtering that the database and product actually need.

Recommended initial filters:

```text
search
status
```

Possible search fields:

```text
username
email
first_name
last_name
```

Only add track/level filters after enrollment/curriculum relationships are defined.

Tenant scope must be applied before filtering.

---

# 10. Pagination

Follow the repository's established pagination behavior.

Do not invent a second pagination format.

The API must clearly document:

```text
count
next
previous
results
```

if that is the repository's standard.

If the project uses another established DRF pagination shape, reuse it exactly.

---

# 11. GET Student Detail

The detail route:

```text
GET /api/organizations/<organization_pk>/students/<id>/
```

must resolve the student through the academy-scoped enrollment relation.

Do not:

```python
User.objects.get(pk=student_id)
```

and then assume that user belongs to the academy.

Correct logic is conceptually:

```text
organization
→ enrollment
→ student user
```

This is a critical tenant-safety rule.

---

# 12. POST Student — Safe Initial Contract

Do not invent an anonymous public student-creation workflow.

For the first backend iteration, choose one of these explicitly:

### Preferred minimal contract

Allow an authorized academy manager to attach an **existing student account**:

```json
{
  "user": 123
}
```

Server behavior:

```text
verify manager permission
→ resolve user
→ verify user.role == student
→ create academy enrollment
→ return enrollment/student representation
```

This is the smallest safe change.

It does not require:

- invitation email
- password creation
- account activation
- token generation
- new onboarding state

Those are separate workflows.

---

# 13. New Student Account Creation

Do **not** combine:

```text
create global user
```

with:

```text
enroll student into academy
```

inside a large implicit endpoint unless the product has explicitly decided how student onboarding works.

Instead, document this as:

```text
OPEN / FOLLOW-UP
Student account invitation/creation workflow
```

A future phase can define:

```text
academy admin
→ invite student
→ account creation/claim
→ enrollment
```

without forcing that complexity into this bridge phase.

---

# 14. PATCH Student

The first PATCH contract should be intentionally narrow.

Recommended:

```text
enrollment_status
```

For example:

```json
{
  "status": "inactive"
}
```

Do not allow this endpoint to change:

```text
User.role
Organization
student identity
parent identity
```

Those are separate domain operations.

If first-name/email/profile editing is required, define it as an explicit account-profile contract rather than silently expanding enrollment PATCH.

---

# 15. Enrollment Semantics

The backend must establish a clear meaning:

```text
Enrollment exists
    = student belongs to this academy
```

and:

```text
Enrollment status is active
    = student is currently active in this academy
```

Do not use `OrganizationMembership.status` for this unless the domain model is formally redesigned.

This keeps:

```text
academy authority
```

separate from:

```text
student participation
```

---

# 16. Parent Links

Existing parent links are global.

Do not change `ParentLink` in this phase.

Instead, when exposing parent information for an academy student:

```text
student enrollment
→ student user
→ existing parent link
```

and then apply any academy-specific authorization rules.

Do not infer that a global parent link automatically grants unrestricted academy access.

---

# 17. Permissions

Use the existing academy membership permission system.

Initial write permissions should be limited to the existing membership managers:

```text
owner
admin
```

Teacher/staff read permissions must be explicitly decided from product requirements before widening access.

Do not create a new permission framework.

---

# 18. Cross-Tenant Safety

This is the highest-priority part of the implementation.

Required tests:

### Case A

User is active member of Academy A.

```text
GET /api/organizations/A/students/
```

must succeed where permitted.

### Case B

Same user requests Academy B without active membership.

Expected:

```text
403 or 404
```

according to the repository's established tenant behavior.

### Case C

Academy A manager knows a student ID belonging to Academy B.

```text
GET /api/organizations/A/students/<B-student>/
```

must not return the Academy B student.

### Case D

Student belongs to two academies.

Each academy must see only its own enrollment record.

---

# 19. Concurrency / Duplicate Protection

Two managers must not be able to create duplicate enrollment rows.

Database constraint:

```text
unique(organization, student)
```

Required API behavior for a duplicate:

```text
400 or 409
```

Use whichever status convention already exists for comparable relationship conflicts in the repository.

Do not introduce a new idempotency system solely for this phase.

---

# 20. Transactions

Student enrollment creation should be transactional.

Conceptually:

```text
validate user
→ validate organization permission
→ create enrollment
```

must not leave partial state.

Do not modify booking/scheduling transaction behavior.

This phase should remain isolated from those domains.

---

# 21. Serializer Design

Create explicit serializers for:

```text
StudentListSerializer
StudentDetailSerializer
StudentEnrollmentCreateSerializer
StudentEnrollmentUpdateSerializer
```

Avoid one serializer that does everything.

The serializers should:

- enforce allowed fields
- protect tenant-owned fields
- map validation errors cleanly
- avoid exposing unnecessary account data

---

# 22. Views / ViewSets

Reuse existing repository patterns.

Do not create a second organization-scoping mechanism.

The view should obtain the organization from the URL, verify membership, then query the student enrollment table within that organization.

Do not accept:

```json
{
  "organization": 99
}
```

from the client.

The route already establishes the tenant.

---

# 23. OpenAPI

This phase is not complete until the OpenAPI schema describes the implemented endpoints.

Add:

```text
paths
requestBody
responses
schemas
parameters
authentication
permission notes
pagination
filter parameters
status codes
```

The schema must match implementation.

Do not hand-create frontend interfaces after this change.

The frontend will regenerate from OpenAPI.

---

# 24. API Compatibility

The change must be additive.

Do not modify existing public behavior for:

```text
auth
organizations
memberships
teachers
curriculum
scheduling
assessment
payouts
notifications
audit
imports
```

unless a direct dependency requires a documented compatibility change.

Do not rename existing fields.

Do not change existing enum values.

Do not change existing authentication semantics.

Do not modify existing teacher behavior.

---

# 25. Migration Strategy

Migration must be additive.

Expected:

```text
new student enrollment table
new indexes/constraints
```

No destructive migration of:

```text
User
OrganizationMembership
ParentLink
TeacherProfile
```

without an explicit architecture decision.

Run:

```bash
python manage.py makemigrations --check
python manage.py migrate
```

and inspect the generated migration before applying it.

---

# 26. Backfill

Do not automatically create academy enrollments for every existing `User(role='student')`.

That would silently assign students to academies.

If existing production data needs migration, create a separate, explicit data-migration plan based on actual source relationships.

No guessing.

---

# 27. Tests

Add model tests for:

```text
student role validation
unique academy/student constraint
status behavior
multi-academy student
```

Add API tests for:

```text
student list
student detail
student attach/enroll
student status update
unauthorized access
cross-tenant access
duplicate enrollment
non-student user rejection
missing student user
pagination
search
```

Preserve all existing tests.

Run the full suite.

---

# 28. API Verification Scenarios

Minimum required scenarios:

## Academy owner

```text
login
→ create/list academy
→ list students
→ attach existing student
→ retrieve student
→ change enrollment status
```

## Academy admin

Same supported management flow.

## Teacher

Verify according to the final permission decision.

If teachers are read-only or denied, test that explicitly.

## Cross-academy

```text
same student user
→ Academy A enrollment
→ Academy B enrollment
→ each academy sees only its own record
```

---

# 29. Frontend Contract Handoff

Once this backend phase passes, the frontend can replace its placeholder with the real contract.

The frontend should then consume:

```text
GET  /api/organizations/<id>/students/
POST /api/organizations/<id>/students/
GET  /api/organizations/<id>/students/<id>/
PATCH /api/organizations/<id>/students/<id>/
```

only if all four are actually implemented and documented.

The frontend must regenerate API types from the updated OpenAPI schema.

Do not manually duplicate request/response interfaces if the frontend's generated-client workflow is available.

---

# 30. Frontend Compatibility Goal

The backend phase is successful when the frontend can implement:

```text
/app/students
    ↓
search/filter
    ↓
student list
    ↓
student detail
    ↓
add existing student
    ↓
change enrollment status
```

without:

- mock APIs
- client-side fake records
- guessed endpoints
- direct database assumptions
- cross-tenant shortcuts

---

# 31. Verification Commands

Run all repository-required checks:

```bash
python manage.py check
python manage.py check --deploy --fail-level WARNING
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Also run the repository's OpenAPI/schema validation mechanism.

Use PostgreSQL.

Do not substitute SQLite for validation.

---

# 32. Definition of Ready

Before coding:

- [ ] inspect current organizations tenancy code
- [ ] inspect current accounts models/serializers/views
- [ ] inspect current API contract
- [ ] confirm student role semantics
- [ ] confirm existing parent-link behavior
- [ ] confirm existing permission/mixin conventions
- [ ] choose final model name
- [ ] decide exact status values
- [ ] record any unresolved onboarding questions as OPEN

---

# 33. Definition of Done

Phase 13 is complete only when:

1. Academy-scoped student enrollment model exists.
2. Database uniqueness prevents duplicate academy/student rows.
3. Only student-role users can be enrolled.
4. `/students/` list endpoint exists.
5. Student detail endpoint exists.
6. Supported enrollment/attach endpoint exists.
7. Supported enrollment-status update exists.
8. Tenant scoping is enforced server-side.
9. Existing organization membership rules remain unchanged.
10. ParentLink behavior remains unchanged.
11. No destructive migration has been introduced.
12. OpenAPI matches implementation.
13. Cross-tenant tests pass.
14. Duplicate/concurrency tests pass.
15. Full backend test suite passes.
16. Existing domains still pass their tests.
17. Migration checks pass.
18. Frontend can regenerate a usable contract from OpenAPI.

---

# 34. Explicit Non-Goals

Do not implement in this phase:

- scheduling
- availability
- booking
- attendance
- assessments
- progress dashboards
- teacher assignment
- payroll
- payout logic
- notification delivery
- invitation email system
- public student signup
- bulk student import
- ownership transfer
- a second authorization system

Those remain separate workflows.

---

# 35. Safe Implementation Order

Use this order:

```text
1. Audit current backend
        ↓
2. Add enrollment model
        ↓
3. Add migration
        ↓
4. Add serializers
        ↓
5. Add organization-scoped views
        ↓
6. Add URLs
        ↓
7. Add OpenAPI
        ↓
8. Add model/API tests
        ↓
9. Run full regression
        ↓
10. Regenerate/verify frontend contract
```

Do not implement the frontend against a provisional backend API.

---

# 36. Rollback Strategy

The phase must be rollback-friendly.

Because the change is additive:

```text
new table
+ new endpoints
+ new tests
+ OpenAPI additions
```

rollback should be isolated from:

```text
existing users
existing memberships
existing teachers
existing bookings
existing assessments
existing payouts
```

Do not mix student enrollment changes with unrelated schema migrations.

---

# 37. Governing Principle

> **Student enrollment is an academy relationship, not a replacement for global identity or administrative membership.**

Keep the boundaries:

```text
User
  = who the person is globally

OrganizationMembership
  = what authority the person has inside the academy

StudentEnrollment
  = whether that student participates in that academy
```

That separation lets the backend add Student Management without breaking the existing tenancy architecture.
