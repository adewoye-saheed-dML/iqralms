# IQRA LMS — Cross-Repo Role, Onboarding, Scheduling and Class Access Stabilization
## Backend implementation instructions for Claude Code

Repository: `adewoye-saheed-dML/iqralms`
Target: `main` (or a dedicated stabilization branch created from current `main`)
Status: **BLOCKING STABILIZATION GATE** — do not advance to a new SaaS phase until this is complete.

## 0. Why this work exists

The current backend has the right broad SaaS architecture, but several API permission boundaries still reflect the old single-academy/global-role model while the frontend now uses academy membership roles.

The master plan requires:
- explicit organization membership for tenant access;
- owner/admin, lead teacher, teacher, parent and student journeys;
- scheduling/assessment/progress/pricing/payouts scoped by role;
- Jitsi behind a provider-neutral meeting boundary;
- invitations for teachers, parents and students;
- frontend-ready, documented API contracts;
- no cross-academy access.

The current implementation is not yet at that gate. The goal of this task is not to redesign the whole backend. It is to reconcile the already-built phases so that the backend contract matches the product's role matrix and supports the real user journeys.

Do not create any `staff` product/domain workflow. `staff` may still exist temporarily in historical/internal database code if removing it would be a separate migration decision, but no new endpoint, serializer, permission, UI contract, or user journey may use `staff`.

---

# 1. Locked product role model

There are two concepts and they must remain separate.

### Global identity role
Existing `accounts.User.role`:
- `lead`
- `sub`
- `student`
- `parent`

### Academy membership role
Existing `organizations.OrganizationMembership.role`:
- `owner`
- `admin`
- `teacher`
- `parent`
- `student`

Resolve authorization in an academy from the active `OrganizationMembership` first.

Important combinations:

| Persona | Membership | Global role |
|---|---|---|
| Academy Owner | owner | may be student/parent/etc. |
| Academy Admin | admin | may be any supported account role |
| Lead Teacher | teacher | lead |
| Teacher | teacher | sub |
| Parent | parent | parent |
| Student | student | student |

Do not use `User.role == lead` as a substitute for academy ownership/admin authority.

Do not make `activeRole == teacher` mean that every teacher has lead-teacher powers.

---

# 2. Canonical role/capability matrix

Use this as the backend source of truth for authorization.

| Capability | Owner | Admin | Lead Teacher | Teacher/Sub | Parent | Student |
|---|---:|---:|---:|---:|---:|---:|
| Academy settings | Yes | Yes | No | No | No | No |
| Manage teachers | Yes | Yes | Yes where explicitly needed for teaching workflow | No | No | No |
| Curriculum management | Yes | Yes | Yes | No | No | No |
| Student/enrollment management | Yes | Yes | Limited | Limited/read assigned | No | No |
| Academy scheduling/routing | Yes | Yes | Yes | Own classes/availability | Own children | Own classes |
| Cohort management | Yes | Yes | Yes | No | No | No |
| Waitlist management | Yes | Yes | Yes | No | Own children/waitlist | Own waitlist |
| Placement review | Yes | Yes | Yes | No | No | Submit/own placement only |
| Assessment review queue | Yes | Yes | Yes | No | Linked children | Own assessments |
| Teacher assessment submissions | No special academy-wide access | No special academy-wide access | Own submissions + review | Own submissions | No | No |
| Progress/reporting | Yes | Yes | Yes | Assigned students/own teaching data | Linked children | Own progress |
| Pricing agreement management | Yes | Yes | Yes | No | View only if later explicitly supported | Own applicable price |
| Payout management/generation | Yes | Yes | Yes where existing product policy allows | Own statement | No | No |
| Own teacher payout statement | No need if owner is not a teacher | No need if admin is not a teacher | Yes | Yes | No | No |
| Notifications | Own notifications; academy delivery management per admin policy | Yes | Own | Own | Own | Own |
| Audit log | Yes | Yes | No | No | No | No |

Do not expose a capability merely because the global account role happens to be `lead`.

---

# 3. Fix the permission architecture first

Create a small, reusable backend permission vocabulary for organization-aware capabilities instead of duplicating `role == lead` in every domain app.

Follow repository conventions and keep app-specific permissions where they have genuinely different semantics, but the checks must understand `OrganizationMembership`.

Expected patterns:

```python
has_active_membership(view, request.user)

is_owner_or_admin(...)
is_lead_teacher(...)
is_teacher(...)
is_owner_admin_or_lead_teacher(...)
```

A lead-teacher check should mean, at minimum:

```text
authenticated
+ active membership in URL organization
+ membership role == teacher
+ user.role == lead
```

An owner/admin check should mean:

```text
authenticated
+ active membership in URL organization
+ membership role in {owner, admin}
```

A combined manager check should use the explicit matrix rather than accepting every teacher.

Every tenant-aware endpoint must still have:
1. permission check;
2. tenant-scoped queryset/service;
3. serializer/model integrity checks.

Do not solve access only by hiding frontend tabs.

---

# 4. Scheduling contract corrections

## Current problem

The existing frontend uses `GET .../bookings/teaching/` for owner/admin scheduling views, but that endpoint is teacher-only. This produces:

```text
Access Denied
You don't have permission to view these bookings.
```

The master plan says owner/admin have academy scheduling/routing access.

## Required backend contract

Keep:

```text
GET /api/scheduling/organizations/{organization_pk}/bookings/mine/
```

For student-owned bookings.

Keep:

```text
GET /api/scheduling/organizations/{organization_pk}/bookings/teaching/
```

For a teacher's assigned classes.

Add or refactor an explicit academy-management read endpoint, for example:

```text
GET /api/scheduling/organizations/{organization_pk}/bookings/academy/
```

It must be owner/admin/lead-teacher scoped and return the academy's scheduled booking/session records.

Do not change `/bookings/teaching/` to mean two different things.

The academy schedule endpoint should support the filters the current UI actually needs:
- date/range;
- teacher;
- status;
- optional student/track;
- pagination if the existing API convention uses it.

The queryset must derive ownership from the canonical academic relationships.

## Cohorts

Current `CohortCreateView` is global-role lead-only. The master matrix requires owner/admin and lead teacher cohort management.

Introduce an organization-aware cohort-manager permission:

```text
owner/admin
OR
active membership role teacher + User.role lead
```

Keep ordinary `sub` teacher out of cohort creation.

Add the missing academy cohort list/detail read surface if the frontend needs it. Do not leave a placeholder UI backed by no contract.

## Waitlist

Separate these audiences:

```text
GET .../waitlist/mine/
```

Student or parent:
- student's own entries;
- parent may see linked children's entries.

Teacher/lead/admin management must use a distinct endpoint.

A lead teacher or owner/admin may view/promote teacher waitlist entries according to the matrix. A normal sub-teacher must not automatically gain academy-wide waitlist visibility.

Promotion must remain tenant-safe and must revalidate teacher eligibility/capacity.

## Availability

Keep teacher availability academy-scoped.

A teacher needs their own availability/classes experience. Students/parents may query availability required for booking.

Do not expose other teachers' internal configuration unnecessarily.

---

# 5. Assessment corrections

## Current problem

Assessment review endpoints use a global `IsLeadTeacher` requiring:

```text
User.role == lead
```

Therefore owner/admin users are denied even though the master plan gives them assessment review/reporting access.

## Required split

Create:

```text
AssessmentReviewer
```

meaning:

```text
active organization membership
AND (
    membership role in {owner, admin}
    OR (membership role == teacher AND user.role == lead)
)
```

Use it for:
- review queue;
- assessment detail review surface;
- review action;
- academy-level teacher quality reporting;
- snapshot generation/listing where the matrix gives management access.

Keep:

```text
IsTeacher
```

for teacher-owned submission workflows.

Keep:
- student own assessments;
- parent linked-child assessments.

Do not let ordinary teacher/sub see the review queue.

The final data/query boundary must remain:

```text
reviewer -> academy-scoped assessment rows
teacher -> own submitted assessments only
student -> own assessments
parent -> linked-child assessments
```

---

# 6. Progress corrections

## Current problem

Frontend currently allows teachers into a "snapshots" view, but backend snapshot listing is lead-only. This creates the teacher Access Denied state.

Do not simply broaden lead snapshot access to every teacher.

Create role-specific progress reads.

Recommended API direction:

```text
GET /api/assessment/organizations/{organization_pk}/progress/mine/
```
student only.

```text
GET /api/assessment/organizations/{organization_pk}/progress/child/?student_id=
```
parent + linked child.

```text
GET /api/assessment/organizations/{organization_pk}/progress/teaching/
```
lead teacher + ordinary teacher, scoped to students/classes they are actually assigned to.

```text
GET /api/assessment/organizations/{organization_pk}/snapshots/
```
owner/admin/lead-teacher management/reporting.

The teacher progress endpoint must not become academy-wide.

Define "teacher's students" from authoritative relationships already in the repository:
- assigned bookings;
- active TeacherTrack membership;
- active organization membership;
- relevant enrollment.

Do not infer access from a raw student ID supplied by the client.

If a student has never had a teaching relationship with that teacher in this academy, that student's progress must not appear in the teacher endpoint.

---

# 7. Pricing corrections

## Current problem

`pricing.permissions.IsLeadTeacher` is still global-role-only:

```text
User.role == lead
```

The master plan gives pricing agreement management to Owner/Admin and Lead Teacher.

Replace the permission with an organization-aware pricing-manager rule:

```text
active membership
AND (
    membership role in {owner, admin}
    OR membership role == teacher + global role lead
)
```

Keep student "mine" pricing scoped to the student.

Do not expose another student's agreement.

Do not invent family pricing views beyond what the current product contract supports.

---

# 8. Payout corrections

The backend payout permissions already understand owner/admin versus teacher. Do not break that logic.

The main problem is the frontend contract pointing owner/admin at a teacher self-service statement endpoint.

Backend must expose a clean manager surface for:
- academy-wide payouts;
- teacher statements;
- generation;
- finalization.

Existing routes are already close:

```text
GET .../lead/
GET .../statements/
POST .../generate/
POST .../<id>/finalize/
GET .../mine/
GET .../statements/mine/
```

Make their permissions consistently match:

```text
manager:
owner/admin/lead-teacher where product policy allows

teacher self-service:
active teacher membership only
```

The API response shapes must stay separated:
- manager sees `teacher`;
- teacher self-service does not need a redundant teacher identity.

---

# 9. Student management corrections

## Current problem

Frontend teacher navigation points at the admin-only student enrollment endpoint, producing:

```text
Only an organization owner or administrator can manage members.
```

Do not grant teachers unrestricted student-management permissions just to remove the error.

Create role-specific reads.

Recommended contract:

```text
GET /api/organizations/{organization_pk}/students/
```

Owner/admin:
- enroll;
- change enrollment;
- manage academy student state.

```text
GET /api/organizations/{organization_pk}/students/mine/
```

Lead/teacher:
- read students actually assigned to their teaching relationship;
- no academy-wide enrollment management.

Parent:
- read linked children only.

Student:
- do not expose the admin student directory as a navigation feature.

Keep enrollment creation/change owner/admin only unless a specific lead-teacher workflow is already documented.

---

# 10. Invitations: teacher, parent and student

The current invitation model is useful and should remain the source of truth.

Required roles:

```text
teacher
parent
student
```

The invitation token must be:
- high entropy;
- stored hashed/digested;
- expiring;
- single-use;
- bound to organization;
- bound to intended email;
- bound to intended organization role.

## Existing-account acceptance

Authenticated user:

```text
POST /api/organizations/{id}/invitations/accept/
{ "token": "..." }
```

must verify:
- invitation exists and is pending;
- not expired;
- organization matches;
- authenticated email matches invitation email;
- requested role is compatible;
- user is not already a member.

Then:
1. create active membership;
2. mark invitation accepted;
3. audit.

## Brand-new account acceptance

Add a public invitation-onboarding endpoint rather than forcing a brand-new invitee through ordinary public registration.

Recommended shape:

```text
POST /api/organizations/{organization_pk}/invitations/accept-and-register/
```

Request should contain:
- invitation token;
- email is derived from invitation and MUST NOT be trusted from the client;
- username;
- first name;
- last name;
- password;
- timezone;
- optional date of birth where the existing registration rules require it.

The backend derives the global role from the invitation:

```text
teacher invitation -> User.role = sub
parent invitation  -> User.role = parent
student invitation -> User.role = student
```

Then atomically:
1. revalidate invitation;
2. create `User`;
3. create `OrganizationMembership`;
4. mark invitation accepted;
5. create audit event;
6. return authentication token/session data compatible with the existing DRF token flow.

Never allow the client to submit:

```json
{ "role": "lead" }
```

to become a teacher through an invitation.

For a teacher invitation, `OrganizationMembership.role = teacher` is the academy role; `User.role = sub` is the global teaching identity.

For student invitations, keep `StudentEnrollment` conceptually separate. Accepting a student invitation grants academy membership; enrollment into a track/level is an academy placement step unless the current contract explicitly carries initial placement information.

For parent invitations, do not invent a new guardianship model. Keep `ParentLink` for the real-world child relationship.

---

# 11. Invitation email delivery

Configure the actual email backend.

Development/SMTP environment should support:

```env
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=1
EMAIL_USE_SSL=0
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=...
```

Use environment variables only.

For Gmail, the credential must be an App Password, not the mailbox's normal password.

Invitation delivery status must remain observable:
- sent;
- failed + useful error code/message.

The API should never report "email sent" when the provider failed.

The invitation itself should remain valid when delivery fails so an admin can resend after correcting configuration.

---

# 12. Provider-neutral Jitsi class meeting access

The master plan requires Jitsi behind a provider-neutral boundary.

Do not move Jitsi URL construction into frontend React components.

Backend responsibilities:
- keep the Phase 9 provider abstraction;
- resolve meeting for the authoritative booking/session;
- verify caller may access the booking;
- return a safe provider-neutral meeting-access response.

Recommended contract:

```text
GET /api/scheduling/organizations/{organization_pk}/bookings/{booking_id}/meeting/
```

Response concept:

```json
{
  "provider": "jitsi",
  "provider_meeting_id": "...",
  "join_url": "...",
  "display_name": "..."
}
```

Do not expose provider credentials.

Access rules:
- assigned teacher for the booking;
- booked student;
- parent of the booked student;
- owner/admin only when their management workflow legitimately requires session access.

A suspended/non-member or unrelated user must not receive the join URL.

Do not rely on knowing the room URL as authorization.

Meeting access should remain derived from the booking/session's academy relationship.

---

# 13. Jitsi meeting lifecycle

Keep the Phase 9 design:

```text
Booking/session
      ↓
Meeting service
      ↓
MeetingProvider
      ↓
Jitsi adapter
      ↓
stored meeting/provider result
```

Meeting creation remains idempotent.

Repeated creation for the same academic session must not create uncontrolled duplicate rooms.

Do not redesign routing or booking validation for this task.

---

# 14. Error contract

Frontend must never need to guess whether a feature is unsupported or simply mis-permissioned.

Use:
- 401 = unauthenticated;
- 403 = authenticated but not allowed;
- 404 = resource not available in the caller's tenant/scope where existing security conventions require concealment;
- 400 = invalid user input;
- 409 = valid request conflicting with resource state.

Messages should be stable enough for frontend mapping.

Examples:

```text
Only an active teacher can view their teaching schedule.
Only an organization owner or administrator can manage enrollment.
You do not have access to this class.
This invitation was sent to a different email address.
This invitation has expired.
```

Do not leak another academy's existence.

---

# 15. Tests that must be added or corrected

## Role tests

For each endpoint family, test:
- owner allowed where matrix says yes;
- admin allowed where matrix says yes;
- lead teacher allowed where matrix says yes;
- ordinary teacher denied where matrix says no;
- parent/student allowed only for their own family/student surfaces.

## Cross-tenant tests

Prove:
- known object IDs from another academy cannot be used;
- teacher cannot see another academy's student/progress/booking;
- parent cannot see another parent's child;
- student cannot see another student's booking/progress;
- payout records cannot cross academy;
- meeting URLs cannot cross academy.

## Invitation tests

At minimum:
1. existing teacher accepts;
2. brand-new teacher registers from invitation;
3. brand-new parent registers from invitation;
4. brand-new student registers from invitation;
5. wrong email is rejected;
6. expired invitation is rejected;
7. revoked invitation is rejected;
8. reused invitation is rejected;
9. repeated invitation for same pending email remains rejected/controlled;
10. teacher invitation creates `User.role=sub` and membership role `teacher`;
11. parent invitation creates `User.role=parent` and membership role `parent`;
12. student invitation creates `User.role=student` and membership role `student`.

## Email tests

Use the repository's test email backend for automated tests.

Prove:
- invitation email contains the acceptance URL;
- failed delivery is recorded;
- invitation remains available for resend;
- resend rotates token and expiry.

## Scheduling tests

Prove:
- owner/admin academy schedule reads;
- lead teacher scheduling management;
- ordinary teacher sees only own classes;
- parent sees only linked child bookings;
- student sees own bookings;
- cohorts visible/manageable by allowed roles;
- ordinary teacher cannot create cohorts unless explicitly allowed;
- waitlist visibility follows matrix.

## Assessment tests

Prove:
- owner/admin review queue works;
- lead teacher review queue works;
- ordinary teacher sees own submissions only;
- student sees own results;
- parent sees linked-child results.

## Progress tests

Prove:
- owner/admin academy reporting;
- lead teacher teaching progress;
- teacher assigned-student progress;
- parent linked-child progress;
- student own progress;
- teacher cannot read an unrelated student's progress.

## Meeting tests

Prove:
- teacher gets authorized class meeting;
- student gets authorized class meeting;
- parent gets authorized child class meeting;
- unrelated member gets denied;
- another academy gets denied;
- cancelled/ineligible meeting access follows product state;
- repeated meeting creation is idempotent.

---

# 16. OpenAPI is part of the contract

After implementation:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Regenerate/update OpenAPI if the project uses a generated schema artifact.

The frontend must be able to generate its client from the resulting contract.

Document:
- invitation registration endpoint;
- manager scheduling endpoint;
- role-specific student/progress endpoints;
- meeting access endpoint;
- permission requirements;
- request/response shapes;
- errors.

---

# 17. Backend acceptance gate

Do not mark this work complete merely because endpoints return 200 in one browser session.

It is complete only when:

1. role matrix is enforced server-side;
2. owner/admin no longer receives teacher-only 403s;
3. ordinary teachers no longer hit admin-only student-management endpoints;
4. progress access matches the role;
5. assessment review access matches the role;
6. pricing management matches the role;
7. payout management and teacher self-service are separate;
8. teacher/parent/student invitations can create first-time accounts and memberships;
9. invitation email delivery is configured/observable;
10. authorized users can obtain provider-neutral meeting access;
11. Jitsi remains behind the provider boundary;
12. all tenant-isolation tests pass;
13. OpenAPI reflects the actual contract;
14. existing scheduling/routing/assessment/pricing/payout/notification tests remain green.

---

# 18. Do not do these things

Do not:
- reintroduce `staff` into the product;
- create a second user table;
- make `User.role` the tenant permission source;
- solve owner/admin failures by giving them unrestricted teacher endpoints;
- make teacher endpoints academy-wide;
- expose a generic `/students/` admin endpoint to teachers;
- make frontend permissions authoritative;
- embed arbitrary URLs from the client into Jitsi;
- put raw Jitsi room construction into booking/React code;
- couple invitation acceptance to an unrelated registration system;
- create a duplicate enrollment model;
- silently change scheduling/routing semantics;
- invent payment collection.

---

# 19. Suggested implementation order

1. Add/rework shared organization-aware permissions.
2. Fix scheduling manager/teacher/parent/student contracts.
3. Fix assessment reviewer/teacher/family contracts.
4. Fix progress role-specific contracts.
5. Fix pricing manager permissions.
6. Confirm payout manager/self-service contracts.
7. Add role-specific student reads.
8. Implement invitation accept-and-register.
9. Configure/test invitation email delivery.
10. Add booking meeting-access endpoint.
11. Verify Phase 9 provider abstraction remains intact.
12. Add/repair regression and cross-tenant tests.
13. Regenerate OpenAPI.
14. Update backend docs/learning notes.
15. Run complete PostgreSQL test/check gate.
