# Quran Academy — Invitation Email & Bulk Teacher Invitation Implementation

## Purpose

Implement the invitation workflow in the backend so the existing Next.js frontend can render real invitation states for **teachers, parents, and students**, while preserving the repository's tenant, role, OpenAPI, notification, import, and audit architecture.

This is an implementation instruction file for the backend repository:

`adewoye-saheed-dML/iqralms`

The frontend repository is:

`adewoye-saheed-dML/iqralms_fe`

The supplied frontend master specification is the product/UX source of truth. The live backend code, tests, and OpenAPI are authoritative for security and actual API behavior.

---

# 1. Non-negotiable product terminology

## There is no product concept called `staff`

Do **not** introduce, extend, or build new invitation behavior around `staff`.

The product-facing people are:

- **Teacher**
- **Parent**
- **Student**

The administrative authority roles remain:

- **Owner**
- **Admin**

The current repository still contains `OrganizationRole.STAFF = "staff"` and several frontend `staff` feature files. Treat these as existing technical debt/legacy compatibility, not as a product requirement.

### Rules

1. `staff` must not be an assignable invitation role.
2. New invitations must never create a `staff` membership.
3. Parent and student invitations must not use `staff` as a generic fallback.
4. New frontend/API documentation must use `teacher`, `parent`, and `student` terminology.
5. Do not rename a real teacher domain into “staff”. The existing `teachers` feature is the correct domain for teacher configuration, approval, capacity, tracks, scheduling, and payouts.
6. Do not silently delete legacy `staff` database rows if they already exist. Handle existing rows deliberately in a separate compatibility/data-migration step after classifying their actual meaning.

---

# 2. Repository findings that this implementation must respect

## Backend

The current backend already has a real invitation lifecycle:

- `organizations.OrganizationInvitation`
- `InvitationStatus`: `pending`, `accepted`, `expired`, `revoked`
- SHA-256 digest storage for the raw invitation token
- seven-day expiry
- one pending invitation per academy/email
- tenant-scoped invitation list/create endpoint
- invitation acceptance endpoint

Relevant current files:

```text
organizations/models.py
organizations/serializers.py
organizations/views.py
organizations/urls.py
organizations/permissions.py
notifications/services.py
notifications/models.py
notifications/adapters.py
imports/models.py
imports/serializers.py
imports/views.py
imports/services.py
imports/permissions.py
audit_logs/models.py
```

The current invitation creation endpoint is:

```text
POST /api/organizations/{organization_pk}/invitations/
```

The current acceptance endpoint is:

```text
POST /api/organizations/{organization_pk}/invitations/accept/
```

The current invitation service only sends an email for a teacher invitation, and it sends that email directly with `send_mail()`. That is incomplete for the requested product behavior.

The existing notification system already has provider-neutral delivery architecture, but its main `Notification` model requires a real `User` recipient. An invitation can target an email address that does not yet have an account. Therefore, do **not** force pre-registration invitations into the ordinary in-app notification recipient model.

## Frontend

The current frontend already has the real teacher domain:

```text
src/features/teachers/
```

and teacher configuration is already connected to the backend teacher-configuration endpoints.

However, invitation code currently lives under:

```text
src/features/staff/
```

and includes:

```text
staffApi
staffKeys
StaffInviteForm
StaffDirectory
manage_staff
"Teachers & Staff"
"Invite Staff"
role option "staff"
```

This must not be extended.

The frontend master specification explicitly defines the people surface as **Students · Teachers · Parents**, defines `/app/teachers` as the teacher-management route, and defines `/accept-invitation` as the invitation acceptance route.

---

# 3. Target invitation model

Keep `OrganizationInvitation` as the canonical invitation record. Do not create a second invitation model.

The invitation must represent:

```text
academy
recipient email
intended academy role
secure token digest
status
expiry
acceptance timestamp
creation timestamp
```

The current token-digest implementation should remain:

```text
raw token
   ↓
SHA-256 digest stored in DB
   ↓
raw token is only available while constructing the invitation email
```

Never store the raw token in the database.

Never return the raw token from the normal invitation list/detail API.

Never put passwords, access tokens, credentials, provider credentials, or other secrets in invitation/notification payloads.

---

# 4. Correct academy roles for invitations

The invitation contract must support these invitation roles:

```text
admin       # existing capability, retained if already supported
teacher
parent
student
```

It must **not** support:

```text
owner
staff
```

`owner` remains protected because ownership is represented by the owner's membership and is not created by invitation.

`staff` is not a product role for new invitations.

## Backend role mapping

The existing repository correctly separates:

```text
accounts.User.role
```

from:

```text
organizations.OrganizationMembership.role
```

Keep that separation.

Use the following invitation semantics:

| Invitation role | Academy membership role | Required global account role on acceptance |
|---|---|---|
| `teacher` | `teacher` | `lead` or `sub` |
| `parent` | `parent` | `parent` |
| `student` | `student` | `student` |
| `admin` | `admin` | existing account role may remain independent |

Do not automatically turn a parent/student into a teacher or vice versa.

For a new teacher account, the existing account model should continue to use the repository's normal teacher-compatible global role (`sub` for public teacher signup where that is required by the existing registration flow). Do not invent a new global `teacher` user role if the current account model does not have one.

---

# 5. Extend `OrganizationRole` without using `staff` as a fallback

The current `OrganizationRole` has:

```text
owner
admin
staff
teacher
```

To support real academy access for invited parents and students, add:

```text
parent
student
```

The resulting product-facing role set is:

```text
owner
admin
teacher
parent
student
```

Keep `staff` only as a backward-compatibility/legacy database value if existing rows require it. It must not remain in the **assignable invitation choices**.

## Why this is required

The current `StudentEnrollment` model deliberately means academic participation, while `OrganizationMembership` means academy access/authority. Parent and student academy-specific APIs already rely on active academy membership. Therefore, a parent/student invitation needs a real membership role instead of abusing `staff` as a generic placeholder.

Do not collapse `StudentEnrollment` into `OrganizationMembership`.

Do not create an `OrganizationMembership` from an invitation and pretend that this also means the student has an academic enrollment. Those are separate relationships.

---

# 6. Invitation lifecycle

Use the current status values:

```text
pending
accepted
expired
revoked
```

Lifecycle:

```text
OWNER / ADMIN creates invitation
        ↓
PENDING invitation row created
        ↓
individual email delivery attempted
        ↓
recipient opens /accept-invitation
        ↓
recipient logs in or registers
        ↓
backend verifies token + email + intended role
        ↓
active OrganizationMembership created
        ↓
invitation becomes ACCEPTED
```

No membership must be created merely because an email was sent.

No membership must be activated merely because a row was uploaded in a CSV/XLSX file.

Invitation acceptance remains the point where academy access is granted.

---

# 7. Existing-account and new-account acceptance

The current acceptance serializer assumes an authenticated user and verifies:

```text
request.user.email == invitation.email
```

Keep this security rule.

The workflow also needs to work for recipients who do not yet have an account.

## Required user journey

The invitation email should point to:

```text
{FRONTEND_BASE_URL}/accept-invitation?organization={organization_id}&token={raw_token}
```

The frontend acceptance screen can then:

1. validate/display safe invitation information;
2. send the recipient to login if the account already exists;
3. send the recipient to registration if no account exists;
4. preserve the invitation token through the auth/registration journey;
5. call the existing invitation acceptance endpoint after authentication.

Do not create a second authentication system solely for invitations.

## Acceptance checks

On acceptance, validate all of the following server-side:

- token digest resolves to the invitation inside the URL's organization;
- invitation is still `pending`;
- invitation has not expired;
- authenticated email matches the invitation email, case-insensitively;
- invitation is not for `owner` or `staff`;
- requested global account role is compatible with the invitation role;
- the user is not already a member of that organization;
- a suspended existing membership is not silently reactivated;
- the invitation and membership changes are atomic.

The accepted operation must remain:

```text
validate invitation
      ↓
create active membership
      ↓
mark invitation accepted
```

inside one `transaction.atomic()` block.

If anything fails, neither the membership nor the accepted invitation state may remain committed.

---

# 8. Student invitation semantics

A `student` invitation means:

```text
academy access as student
```

It does **not** automatically mean:

```text
track assignment
level assignment
enrollment
placement
parent link
```

Those remain separate product workflows.

After acceptance:

```text
OrganizationMembership(role=student, status=active)
```

may exist without a `StudentEnrollment` yet.

This is consistent with the product's workflow separation:

```text
invite/import student
→ connect to academy/programme
→ confirm enrollment
→ schedule
```

Do not create fake curriculum/enrollment records just to make the invitation appear complete.

---

# 9. Parent invitation semantics

A `parent` invitation means:

```text
academy access as parent
```

It does not guess a child relationship.

Do not create a `ParentLink` from an invitation alone unless the request explicitly contains enough information to identify the correct student and the existing parent-link rules permit it.

The global parent/child relationship remains owned by `ParentLink`.

The academy relationship remains owned by `OrganizationMembership` and the existing organization-scoped child logic.

---

# 10. Teacher invitation semantics

A `teacher` invitation means:

```text
academy membership role = teacher
```

Acceptance must result in a teacher-compatible account (`lead` or `sub`) and an active teacher membership.

Do not create teacher configuration automatically unless the existing teacher-configuration contract explicitly requires it.

The existing post-acceptance teacher workflow can remain:

```text
teacher membership
→ teacher configuration
→ academy approval
→ track assignment
→ availability/scheduling
```

This preserves the current `OrganizationTeacherConfiguration` authority for academy-specific:

- approved status
- max weekly hours
- hourly payout rate

---

# 11. Single invitation API

Keep the current route:

```http
POST /api/organizations/{organization_pk}/invitations/
```

Request:

```json
{
  "email": "teacher@example.com",
  "role": "teacher"
}
```

Supported roles:

```text
admin
teacher
parent
student
```

Do not accept `owner` or `staff`.

## Authorization

Only an active academy:

```text
owner
admin
```

may create invitations.

Use the existing `OrganizationScopedMixin` and `CanManageOrganizationMemberships` path. Do not introduce a second permission system.

## Response

Return a safe invitation representation containing at minimum:

```text
id
email
role
status
expires_at
created_at
email_delivery_status
```

Do not expose:

```text
raw_token
passwords
SMTP credentials
provider secrets
```

A delivery status such as `sent` or `failed` is safe to expose to authorized academy administrators; provider-specific credentials/details are not.

---

# 12. Invitation list API

Keep:

```http
GET /api/organizations/{organization_pk}/invitations/
```

Return invitations scoped strictly to the current academy.

Recommended safe representation:

```json
{
  "id": 42,
  "email": "teacher@example.com",
  "role": "teacher",
  "status": "pending",
  "expires_at": "2026-09-27T21:00:00Z",
  "created_at": "2026-09-20T21:00:00Z",
  "email_delivery_status": "sent"
}
```

Do not return the token.

Do not return another academy's invitations even when an id is guessed.

Keep querysets tenant-scoped before any extra filters.

---

# 13. Invitation preview for `/accept-invitation`

Add a small public/token-authenticated preview endpoint if the frontend needs safe pre-auth rendering:

```http
GET /api/organizations/{organization_pk}/invitations/preview/?token=<raw_token>
```

This endpoint must not create membership or mutate the invitation.

It should return only safe data required by the acceptance page, for example:

```json
{
  "organization_id": 12,
  "organization_name": "Al-Huda Quran Academy",
  "role": "teacher",
  "status": "pending",
  "expires_at": "2026-09-27T21:00:00Z"
}
```

Do not return the full email address unless there is a demonstrated product requirement. The token itself is the capability used to look up the invitation.

If a preview endpoint is not required by the existing frontend implementation, do not invent it merely for completeness. The main acceptance endpoint remains canonical.

---

# 14. Resend invitation

Add an explicit resend operation because invitation tokens are stored only as digests and therefore cannot be recovered once the original email is sent.

Preferred route:

```http
POST /api/organizations/{organization_pk}/invitations/{id}/resend/
```

Rules:

1. owner/admin only;
2. invitation must belong to the selected academy;
3. only `pending` invitations may be resent;
4. generate a new secure raw token;
5. replace the stored digest;
6. refresh expiry according to the canonical invitation TTL;
7. invalidate the old token;
8. attempt exactly one new email delivery;
9. retain the same invitation record and id;
10. do not create duplicate pending invitation rows.

This provides a clean frontend action without exposing token material.

---

# 15. Revoke invitation

Support explicit revocation if the current frontend needs it:

```http
POST /api/organizations/{organization_pk}/invitations/{id}/revoke/
```

Rules:

- owner/admin only;
- academy-scoped;
- pending only;
- transition to `revoked`;
- token can no longer be accepted.

Do not delete invitation history merely to hide a revoked invitation.

If the frontend has no revoke action yet, keep this endpoint optional rather than expanding scope without need.

---

# 16. Email delivery architecture

The current implementation has this defect:

```text
OrganizationInvitation
    ↓
notify_teacher_invitation()
    ↓
direct send_mail()
```

and only the teacher role is emailed.

Replace the role-specific direct email path with one reusable invitation email service, for example:

```text
send_invitation_email(invitation, raw_token)
```

The service may internally use the existing email adapter/provider boundary, but it must remain independent of a logged-in `Notification.recipient` because the invited address may not yet have a `User` account.

## Required behavior

For every newly created invitation:

```text
create invitation
→ retain raw token only in memory
→ build role-specific email
→ send through email provider abstraction
→ record delivery outcome
```

Do not make email provider failure roll back a valid invitation row. A failed email is a failed delivery, not proof that the invitation record itself should disappear.

This makes retry/resend possible.

---

# 17. Invitation delivery record

Do not overload `Notification.recipient` with a fake user just to support an unregistered invite.

Prefer a small dedicated delivery record associated with `OrganizationInvitation`, within the existing notifications/delivery architecture.

Conceptually:

```text
InvitationDelivery
    id
    invitation
    channel = email
    provider
    status
    attempt_count
    provider_message_id
    error_code
    error_message
    attempted_at
    delivered_at
    created_at
```

Keep provider credentials/secrets out of the database and out of API responses.

The frontend needs the high-level delivery outcome, not provider internals.

If the repository's existing delivery model can be safely generalized to handle an invitation with no `User` recipient, reuse it instead of creating duplicate delivery infrastructure. Do not create two competing provider systems.

---

# 18. Email content

Use one common invitation template with role-specific wording.

The email must include:

```text
academy name
invited role
clear call-to-action
invitation expiry
```

The call-to-action must contain the frontend acceptance URL:

```text
{FRONTEND_BASE_URL}/accept-invitation?organization={organization_id}&token={raw_token}
```

Do not include a password or temporary password in the invitation email.

Suggested subject pattern:

```text
Invitation to join {academy_name}
```

The body must change naturally by role:

```text
teacher → invited to join the academy as a teacher
parent  → invited to join the academy as a parent
student → invited to join the academy as a student
admin   → invited to join the academy as an administrator
```

Do not use “staff” anywhere in these messages.

---

# 19. Frontend base URL configuration

Add one environment-backed setting for the acceptance-link origin:

```text
FRONTEND_BASE_URL
```

Development example:

```text
http://localhost:3000
```

Production must use the real frontend HTTPS origin.

Do not hard-code localhost into an email service.

The backend should construct the link centrally so the frontend does not need to reconstruct token URLs itself.

---

# 20. Duplicate and conflict rules

Normalize email consistently:

```text
USER@Example.COM
→ user@example.com
```

The current model already prevents more than one pending invitation for the same academy/email. Preserve this constraint.

Rules:

### Existing active member

Reject the invitation request.

Do not change their role.

Do not create another membership.

### Existing suspended member

Reject the invitation request.

Do not silently reactivate them.

A separate explicit reactivation action already exists for membership management.

### Existing pending invitation

Reject duplicate creation.

Use resend only through the explicit resend operation.

### Accepted/expired/revoked historical invitation

A new invitation may be created after the old invitation is no longer pending, subject to the normal membership/conflict checks.

---

# 21. Bulk teacher invitation through the existing import system

Do **not** invent a second upload framework.

The backend already has:

```text
imports.ImportJob
POST /api/imports/organizations/{organization_pk}/validate/
POST /api/imports/organizations/{organization_pk}/{id}/commit/
```

and `ImportKind.TEACHERS`.

Use the existing two-stage pattern:

```text
upload
  ↓
parse
  ↓
validate
  ↓
preview result
  ↓
commit
  ↓
create invitations
  ↓
send one email per teacher
  ↓
report results
```

## Critical change

For `kind=teachers`, the commit operation must no longer create an active teacher membership directly as the primary onboarding result.

Instead:

```text
valid teacher row
   ↓
OrganizationInvitation(role=teacher, status=pending)
   ↓
email invitation
```

The actual membership is created only after invitation acceptance.

This is the important distinction between:

```text
bulk import
```

and:

```text
academy admission through invitation
```

---

# 22. Bulk teacher upload file contract

For the invitation-specific teacher import, make the canonical minimum:

```text
email
```

Optional columns may be accepted if the existing import UI already supplies them:

```text
first_name
last_name
timezone
```

Do not require meaningless profile fields simply to send an invitation.

Do not allow organization ids, membership roles, owner flags, or arbitrary model fields inside the spreadsheet.

A row such as:

```csv
email,first_name,last_name,timezone
ustadh@example.com,Ustadh,Ahmad,Africa/Lagos
teacher2@example.com,Ali,Yusuf,Europe/London
```

should produce two independent invitations.

One invalid row must not corrupt another row.

---

# 23. Bulk import validation rules for teachers

Validation must detect at minimum:

- empty email;
- malformed email;
- duplicate email within the same file;
- duplicate pending invitation in the same academy;
- existing active academy membership;
- existing suspended academy membership;
- conflicting existing account role where the invitation cannot be accepted as a teacher;
- malformed spreadsheet structure;
- unsupported file type;
- oversized upload.

The existing 5 MB CSV/XLSX validation limit may remain unless the product explicitly changes it.

The validation stage must be read/preview only.

No invitation, user, membership, or teacher configuration may be created during validation.

---

# 24. Bulk commit and email failure semantics

The database part of the commit must be transactional.

The external email provider is not transactional, so do **not** keep a database transaction open while assuming SMTP/provider success is guaranteed.

Recommended sequence:

```text
validate import
     ↓
transaction.atomic()
     ↓
create invitation rows
     ↓
commit DB transaction
     ↓
attempt one email per invitation
     ↓
record each delivery result
     ↓
complete import result
```

Use `transaction.on_commit()` where appropriate so emails are never sent for invitation rows that were later rolled back.

If 20 invitations are created and 18 emails send successfully while 2 fail:

```text
invitations_created = 20
emails_sent = 18
emails_failed = 2
import_status = partially_completed
```

The two failed invitations must remain retryable.

Do not delete the two invitation rows simply because the provider failed.

---

# 25. Import result contract

Extend the existing import response enough for the frontend to render the actual outcome for teacher invitations.

At minimum expose counts equivalent to:

```text
total_rows
valid_rows
invalid_rows
invitations_created
emails_sent
emails_failed
skipped_rows
```

The repository may reuse existing fields such as `created_count`, `skipped_count`, `error_count`, and `error_report`, provided the meaning is documented accurately.

Do not overload `updated_count` with a different meaning without documenting it.

For row errors, retain structured objects such as:

```json
{
  "row": 7,
  "field": "email",
  "code": "duplicate_pending_invitation",
  "message": "A pending invitation already exists for this email."
}
```

---

# 26. Parent/student single invitations vs imports

The immediate requirement is:

```text
single email invitation → parent
single email invitation → student
single email invitation → teacher
bulk file invitation → teachers
```

Do not redesign student/parent imports unless needed for the invitation contract.

Existing CSV/XLSX student/parent imports may continue to support their existing explicit account/membership workflows, but they must not introduce new `staff` records. If those flows currently map parent/student membership to `OrganizationRole.STAFF`, correct those mappings as part of this invitation/role cleanup:

```text
student import → OrganizationRole.STUDENT
parent import  → OrganizationRole.PARENT
teacher import → OrganizationRole.TEACHER
```

Do not reactivate suspended memberships silently during imports.

---

# 27. Permission updates

Keep owner/admin authorization as the single backend membership-management gate.

Do not introduce:

```text
IsTeacherAdmin
IsParentAdmin
InvitationPermissionSystem2
```

The existing permission infrastructure should answer:

```text
CanManageOrganizationMemberships
```

and the invitation serializer/service should enforce which roles can be assigned.

A teacher, parent, or student must not be able to create invitations merely because their frontend hides the button. The backend must reject unauthorized calls.

---

# 28. Audit logging

Invitation operations are important tenant actions and should be auditable.

Add explicit audit actions where the current audit contract does not already provide them:

```text
invitation.created
invitation.resent
invitation.revoked
invitation.accepted
invitation.email_failed
```

For a bulk teacher import, retain the existing import audit events and include invitation/email outcome counts in metadata where appropriate.

Audit metadata must not include:

```text
raw invitation token
passwords
SMTP credentials
provider secrets
```

The audit target must remain tenant-scoped.

---

# 29. API/OpenAPI contract

Every API-affecting change must update OpenAPI alongside the backend implementation.

Do not hand-edit the generated frontend TypeScript schema.

Required public contract changes include:

```text
invitation role enum
invitation response fields
email delivery status
bulk teacher import behavior
acceptance/preview behavior if added
resend/revoke endpoints if added
```

After implementation:

```bash
python manage.py spectacular --file schema.yml --validate
```

Then regenerate/update the frontend generated API schema using the frontend repository's normal generation flow.

The generated frontend client/types remain derived artifacts.

---

# 30. Frontend contract changes required after backend completion

The backend implementation is the immediate focus, but the API must be shaped so the existing frontend can render it correctly.

## Remove `staff` terminology from product-facing invitation UX

The following frontend concepts must eventually be replaced:

```text
src/features/staff/
staffApi
staffKeys
StaffInviteForm
StaffDirectory
manage_staff
Teachers & Staff
Invite Staff
No staff members
role = staff
```

Replace them with an invitation/teacher-oriented domain, for example:

```text
invitationsApi
invitationKeys
TeacherInviteForm
TeacherDirectory
manage_invitations
Teachers
Invite Teacher
```

The exact component file names may follow the frontend repository's architecture, but the product vocabulary must not say `staff`.

## Teacher page

Keep:

```text
/app/teachers
```

because that is already the correct teacher route.

Invitation controls belong on the teacher-management experience, not on a generic staff screen.

## Onboarding

Change:

```text
Teachers & Staff
```

to:

```text
Teachers
```

and:

```text
Invite Staff
```

to:

```text
Invite Teacher
```

The onboarding step may show invitation counts once the backend exposes them.

## Acceptance route

Keep:

```text
/accept-invitation
```

and make the frontend render:

```text
valid
expired
revoked
already accepted
wrong account email
successful acceptance
```

based on real backend responses.

## Bulk teacher upload

Use the existing import workflow:

```text
/app/imports
```

with a teacher invitation mode using:

```text
upload
→ validate
→ show row errors
→ confirm
→ commit
→ show invitation/email result
```

Do not build a separate upload engine in the teachers screen.

---

# 31. Frontend state expectations from the API

The API must provide enough information for these UI states:

### Loading

Normal query/mutation loading state.

### Empty

No pending invitations.

### Pending

Invitation exists and has not been accepted.

### Email sent

Invitation exists and the latest email attempt succeeded.

### Email failed

Invitation exists but the latest delivery failed; frontend may show a retry/resend action if exposed.

### Expired

Invitation cannot be accepted; resend can create a fresh token.

### Revoked

Invitation cannot be accepted.

### Accepted

Invitation has already produced its membership and cannot be reused.

### Validation error

Return field-level DRF validation data so the frontend can attach errors to the email/role input or import row.

Do not force the frontend to parse raw Python exception strings.

---

# 32. Tenant safety requirements

Every invitation operation must derive academy access from:

```text
authenticated user
    ↓
active membership in URL academy
    ↓
owner/admin permission
```

Never trust:

```text
organization_id in request body
role in request body as proof of authority
user id supplied by the client as proof of identity
```

The invitation's organization must come from the URL/scoped server state.

A known invitation id or token belonging to Academy A must not be usable against Academy B.

A suspended owner/admin membership must not create invitations.

A teacher/parent/student must not list another academy's invitations.

---

# 33. Concurrency and idempotency

The current database constraint:

```text
unique pending invitation per organization + email
```

must remain.

Resend must not race into duplicate pending records.

Bulk import commit must lock or otherwise safely coordinate rows/jobs so two simultaneous commits cannot create duplicate invitations.

Use the existing `select_for_update()` pattern on the import job where appropriate.

Do not rely only on Python-side `exists()` checks; the database uniqueness constraint remains the final guard.

---

# 34. Tests — backend

Add/extend tests before calling this work complete.

## Invitation creation

- owner can invite teacher;
- admin can invite teacher;
- owner can invite parent;
- owner can invite student;
- owner can invite admin if admin invitations remain enabled;
- teacher cannot invite;
- parent cannot invite;
- student cannot invite;
- `owner` role is rejected;
- `staff` role is rejected;
- malformed email is rejected;
- duplicate pending invitation is rejected;
- existing active member cannot be invited;
- existing suspended member cannot be invited silently.

## Token security

- raw token is never persisted;
- digest lookup works;
- expired token is rejected;
- revoked token is rejected;
- accepted token cannot be reused;
- wrong organization is rejected;
- wrong authenticated email is rejected.

## Role semantics

- teacher invitation accepts only teacher-compatible global accounts;
- parent invitation accepts parent accounts;
- student invitation accepts student accounts;
- parent invitation does not create a child link automatically;
- student invitation does not create fake enrollment automatically;
- teacher invitation creates `OrganizationRole.TEACHER`, never `STAFF`.

## Acceptance atomicity

Test:

1. valid invitation creates active membership and becomes accepted;
2. existing active membership is rejected;
3. existing suspended membership is rejected;
4. rejected acceptance leaves invitation pending;
5. rejected acceptance does not alter membership role/status;
6. transaction failure rolls back both membership and invitation acceptance.

## Email

- teacher email sent;
- parent email sent;
- student email sent;
- email contains academy and acceptance URL;
- email does not contain password or provider secrets;
- provider failure is recorded as a failed delivery;
- provider failure does not delete the invitation;
- resend generates a new token;
- old resend token stops working.

## Tenant isolation

- Academy A cannot create invitations in Academy B;
- Academy A cannot list Academy B invitations;
- Academy A cannot resend/revoke Academy B invitations;
- Academy A invitation token cannot be accepted through Academy B.

---

# 35. Tests — bulk teacher upload

Add tests for:

- valid CSV creates one invitation per valid teacher row;
- valid XLSX creates one invitation per valid teacher row;
- duplicate email inside a file creates a row error;
- duplicate existing pending invitation creates a row error;
- existing active member creates a row error;
- suspended membership creates a row error and is not reactivated;
- validation stage creates zero invitation rows;
- commit creates invitations only for validated rows;
- invalid rows do not block valid rows when partial import is the chosen documented behavior;
- each valid invitation gets its own email attempt;
- one email failure does not delete other invitations;
- result counts are accurate;
- import job remains tenant-scoped;
- concurrent commit cannot create duplicates.

Also verify the existing student/parent import tests no longer map new records to `OrganizationRole.STAFF`.

---

# 36. Tests — OpenAPI/frontend compatibility

Verify the generated OpenAPI schema contains:

```text
teacher
parent
student
```

in the appropriate public invitation contract and does not expose `staff` as an assignable invitation role.

Verify generated frontend types are regenerated from OpenAPI and that no hand-maintained duplicate interface is introduced.

Verify the frontend can represent:

```text
pending
accepted
expired
revoked
email sent
email failed
```

without provider-specific fields.

---

# 37. Important cleanup of current backend import behavior

Current `imports/services.py` contains the following incorrect product mapping for parents/students:

```text
teachers  → OrganizationRole.TEACHER
students  → OrganizationRole.STAFF
parents   → OrganizationRole.STAFF
```

Change the latter two to:

```text
students  → OrganizationRole.STUDENT
parents   → OrganizationRole.PARENT
```

For the teacher import, the new invitation path should be:

```text
teacher import
→ OrganizationInvitation(role=teacher)
→ email
→ acceptance
→ OrganizationMembership(role=teacher)
```

Do not use the import commit as an undocumented shortcut around invitation acceptance.

---

# 38. Existing teacher notification function

The current function:

```text
notifications.services.notify_teacher_invitation()
```

should not remain the only invitation-email implementation.

Refactor it into the generic invitation delivery path or make it a thin compatibility wrapper around:

```text
send_invitation_email()
```

so the same delivery rules apply to:

```text
teacher
parent
student
admin
```

No provider-specific logic should be duplicated per role.

---

# 39. API error contract

Use predictable DRF errors.

Examples:

```json
{
  "email": ["A pending invitation already exists for this email."]
}
```

```json
{
  "role": ["This role cannot be invited."]
}
```

```json
{
  "token": ["This invitation has expired."]
}
```

Do not return a raw traceback.

Do not leak unrelated tenant existence through error differences.

---

# 40. Do not make unrelated changes

This task must stay focused on:

```text
invitation lifecycle
invitation email delivery
parent/student/teacher invitation roles
bulk teacher invitation import
role cleanup around staff misuse
OpenAPI contract
invitation tests
```

Do not rewrite:

- scheduling;
- curriculum;
- assessment;
- payout calculations;
- video provider logic;
- unrelated frontend pages;
- authentication token architecture.

Reuse existing tenant, notification, import, and audit infrastructure.

---

# 41. Implementation order

Execute in this order:

## Step 1 — Inspect actual live contracts

Re-read:

```text
organizations/models.py
organizations/serializers.py
organizations/views.py
organizations/permissions.py
accounts/models.py
accounts/serializers.py
notifications/services.py
notifications/models.py
notifications/adapters.py
imports/services.py
imports/views.py
imports/models.py
audit_logs/models.py
schema.yml
```

Also inspect the frontend invitation/teacher files before changing public API names.

## Step 2 — Correct role model

Add `parent` and `student` academy membership roles.

Remove `staff` from all new assignable invitation paths.

Update the parent/student import mapping so new records do not receive `staff`.

Do not silently migrate existing ambiguous `staff` rows.

## Step 3 — Generalize invitation creation

Extend `OrganizationInvitationCreateSerializer` to support:

```text
admin
teacher
parent
student
```

with explicit role validation.

## Step 4 — Generalize invitation email delivery

Build one invitation email service capable of sending role-specific invitation content.

Use the secure raw token only during construction of the email.

Record delivery outcome.

## Step 5 — Extend invitation acceptance

Preserve current atomic acceptance and add role compatibility checks.

Ensure existing active/suspended memberships are never silently changed.

## Step 6 — Add bulk teacher invitation behavior

Rewire the existing `teachers` import commit from:

```text
create user + create active membership
```

to:

```text
create pending invitation + send invitation email
```

while preserving the existing upload → validate → commit architecture.

## Step 7 — Add resend/retry support

Implement explicit resend behavior so failed deliveries can be retried without creating duplicate pending invitations.

## Step 8 — Update audit events

Record invitation lifecycle events without storing secrets.

## Step 9 — Update OpenAPI

Regenerate and validate `schema.yml`.

## Step 10 — Run backend tests

At minimum:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py spectacular --file schema.yml --validate
python manage.py test organizations
python manage.py test accounts
python manage.py test notifications
python manage.py test imports
python manage.py test audit_logs
python manage.py test
```

Run against PostgreSQL; do not substitute SQLite.

## Step 11 — Regenerate frontend API types

Update the frontend generated client/schema from the backend OpenAPI contract.

## Step 12 — Remove frontend `staff` invitation implementation

The frontend can then consume the real invitation contract under the teacher/invitations domains.

---

# 42. Definition of Done

This work is complete only when all of the following are true:

- [ ] owner/admin can invite a teacher by email;
- [ ] owner/admin can invite a parent by email;
- [ ] owner/admin can invite a student by email;
- [ ] admin invitation remains correct if retained;
- [ ] owner cannot be invited;
- [ ] staff cannot be invited;
- [ ] no new parent/student records use `OrganizationRole.STAFF`;
- [ ] teacher invitations create `OrganizationRole.TEACHER` only;
- [ ] invitation email is sent for every supported role;
- [ ] invitation contains the secure acceptance link;
- [ ] raw token is never stored or returned;
- [ ] invitation has a clear pending/accepted/expired/revoked lifecycle;
- [ ] acceptance is atomic;
- [ ] existing active memberships are never changed by invitation acceptance;
- [ ] suspended memberships are never silently reactivated;
- [ ] bulk CSV teacher upload creates one pending invitation per valid row;
- [ ] bulk XLSX teacher upload creates one pending invitation per valid row;
- [ ] bulk validation makes no membership/invitation mutations;
- [ ] bulk email failures are reported without deleting invitation records;
- [ ] invitation resend generates a fresh token;
- [ ] invitation data is tenant-isolated;
- [ ] invitation actions are auditable without secrets;
- [ ] OpenAPI accurately describes the real endpoints and enums;
- [ ] frontend generated API types are regenerated from OpenAPI;
- [ ] backend invitation behavior is covered by regression tests;
- [ ] no new implementation uses the product term `staff`;
- [ ] the frontend can render the resulting invitation lifecycle from the API.

---

# 43. Final architectural target

The final architecture should be:

```text
                         ┌────────────────────┐
                         │ Owner / Admin       │
                         │ invites person      │
                         └─────────┬──────────┘
                                   │
                                   ▼
                      OrganizationInvitation
                         pending + token digest
                                   │
                     ┌─────────────┴─────────────┐
                     │                           │
                     ▼                           ▼
             single invitation            teacher CSV/XLSX
                     │                           │
                     │                    validate → commit
                     │                           │
                     └─────────────┬─────────────┘
                                   ▼
                         Invitation Email Service
                                   │
                                   ▼
                           Email Delivery Record
                                   │
                                   ▼
                       /accept-invitation
                                   │
                     login or registration first
                                   │
                                   ▼
                         server-side validation
                                   │
                                   ▼
                    active OrganizationMembership
                    ┌────────┬────────┬────────┐
                    ▼        ▼        ▼        ▼
                  teacher   parent   student   admin
                    │
                    ▼
             teacher configuration
             / assignment / scheduling
```

The key invariant is:

```text
INVITATION ≠ MEMBERSHIP
```

Invitation is the pending onboarding object.

Membership is the granted academy access.

Enrollment is the student's academic placement.

ParentLink is the global family relationship.

Teacher configuration is the academy's teaching configuration.

Keep these responsibilities separate.

---

# 44. Source-alignment notes

This implementation is deliberately aligned to the current project structure:

- The frontend master specification defines the people surface as Students, Teachers, and Parents.
- The specification defines `/accept-invitation` and `/app/teachers`.
- The specification requires owner/admin teacher management and explicitly includes invitations in the admin attention area.
- The specification requires tenant-aware API access, generated OpenAPI types, role-aware UX, loading/error/success states, and an E2E journey where an admin invites a teacher and the teacher sees the correct academy context.
- The backend already has the canonical `OrganizationInvitation` model and invitation acceptance path.
- The notification domain already has provider-neutral delivery concepts, but ordinary notifications require a real user recipient, so unregistered invitations need a dedicated invitation-delivery bridge rather than a fake user.
- The import domain already supports CSV/XLSX and the validate → commit workflow, so bulk teacher invitations should extend that workflow rather than create another upload system.

Do not treat this file as a competing product specification. If the live OpenAPI schema or backend tests reveal a real mismatch, resolve the contract explicitly and update the relevant SSoT/documentation rather than silently drifting.
