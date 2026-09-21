# Quran Academy — First-Time Teacher Invitation Onboarding (Backend)

## Goal

Implement the backend side of a first-time teacher invitation flow.

A teacher who has **no existing Quran Academy account** must be able to click the invitation email, create their account, and be added to the inviting academy as a `teacher` without first creating a public account and without manually finding/requesting the academy.

This must use the existing invitation, user, membership, token-authentication, audit-log, and validation architecture. Do not introduce a second authentication system.

## Repository facts already verified

Repository: `adewoye-saheed-dML/iqralms`

Relevant existing code:

- `accounts/models.py`
  - `Role.LEAD = "lead"`
  - `Role.SUB = "sub"`
  - `Role.STUDENT = "student"`
  - `Role.PARENT = "parent"`
  - `User.is_teacher` is true for `lead` and `sub`.
  - `TeacherProfile` may only belong to `lead`/`sub` accounts.
- `accounts/serializers.py`
  - `RegisterSerializer` is the existing public registration serializer.
  - `SELF_REGISTERABLE_ROLES` currently excludes teacher-facing wording and exposes `sub` rather than a product-level `teacher` account role.
- `accounts/views.py`
  - `POST /api/auth/register/` creates an account but deliberately creates **no organization membership**.
- `config/urls.py`
  - auth routes are mounted explicitly.
- `organizations/models.py`
  - `OrganizationRole.TEACHER = "teacher"`
  - `OrganizationInvitation` is the invitation lifecycle.
  - invitation tokens are stored as SHA-256 digests.
  - organization membership is unique per `(organization, user)`.
- `organizations/urls.py`
  - existing invitation routes:
    - `POST /api/organizations/{organization_pk}/invitations/`
    - `GET /api/organizations/{organization_pk}/invitations/`
    - `GET /api/organizations/{organization_pk}/invitations/preview/?token=...`
    - `POST /api/organizations/{organization_pk}/invitations/accept/`
    - resend/revoke routes already exist.
- `organizations/serializers.py`
  - `OrganizationInvitationCreateSerializer` creates pending invitations.
  - `OrganizationInvitationAcceptSerializer` currently requires an authenticated user.
  - For a teacher invitation it currently requires the authenticated user's global role to be `lead` or `sub`.
  - It verifies the authenticated user's email matches the invitation email.
  - It creates the `OrganizationMembership` with role `teacher` and marks the invitation accepted.
- `organizations/views.py`
  - invitation create/preview/accept/resend/revoke views already exist.
  - preview is public (`AllowAny`).
  - accept is authenticated only.
- `notifications/services.py`
  - invitation email generation already exists and the email link contains organization + token.
- `config/settings.py`
  - email backend is currently console backend in the verified default branch; SMTP work is separate from this onboarding task.

## Product/domain rule

There is **no product/domain concept called `staff`** in this feature.

Do not add, restore, document, or display `staff` anywhere in the new flow.

For a teacher invitation:

- Invitation role = `teacher` (organization role).
- New user's global `accounts.Role` = `sub` so `User.is_teacher` is true.
- New user's organization membership role = `teacher`.

Do not expose `sub` to the teacher as a product choice. The invitation determines that role internally.

## Required backend design

### 1. Add invitation-based account registration

Add a public endpoint dedicated to a pending invitation, for example:

```text
POST /api/organizations/{organization_pk}/invitations/register/
```

Use naming consistent with the existing organization invitation routes. Keep the exact URL stable across backend and frontend.

The endpoint must be `AllowAny` because the person does not have an account yet.

### 2. Request contract

The invitation token and organization identify the invitation. The email must be taken from the invitation, not trusted from the request body.

Suggested request shape:

```json
{
  "token": "raw invitation token",
  "first_name": "Amina",
  "last_name": "Yusuf",
  "password": "strong-password",
  "timezone": "Africa/Lagos",
  "date_of_birth": null
}
```

Do not require the frontend to submit the invitation email as an authority field. The serializer should load the invitation and use `invitation.email` as the account email.

Username still exists in `AbstractUser`, but the first-time invitation flow should not force the teacher to invent one. Generate a unique username server-side from the invited email/local-part with a deterministic collision-safe suffix strategy.

Example acceptable outcomes:

```text
amina.yusuf
amina.yusuf2
amina.yusuf3
```

The exact normalization helper may follow repository conventions.

### 3. Validation order

The registration serializer should:

1. Require a non-empty token.
2. Hash the raw token with the same SHA-256 method already used by `OrganizationInvitationAcceptSerializer`.
3. Restrict lookup to `organization_pk` from the URL.
4. Require invitation status `pending`.
5. Require `invitation.is_valid()`; when expired, update the invitation to `expired` before returning the validation error, matching existing behavior.
6. Only support invitation roles that have a valid global account-role mapping.
7. For a teacher invitation, map to `Role.SUB`.
8. Check whether a user already exists for `invitation.email` (case-insensitive).
9. If the user already exists, do not create a second account. Return a clean client error such as HTTP 409 if the project conventions support it, otherwise a DRF 400 with a stable error message/code that the frontend can recognize and route to login.
10. Validate password using the existing `django.contrib.auth.password_validation.validate_password` behavior already used by `RegisterSerializer`.
11. Validate timezone and date-of-birth using the same model/serializer rules already used by normal registration.

Do not accept a request-supplied organization role or global role.

### 4. Transactional creation

Create the account, membership, invitation acceptance, token-auth record, and audit event in a transaction.

Recommended sequence:

```text
lock invitation
  ↓
validate pending + unexpired
  ↓
create User
  - email = invitation.email
  - role = Role.SUB for teacher invitation
  - password = set_password()
  - is_minor derived using existing User.minor_from_date_of_birth()
  ↓
create OrganizationMembership
  - organization = invitation.organization
  - user = new user
  - role = invitation.role
  - status = active
  ↓
mark invitation accepted
  - accepted_at = timezone.now()
  ↓
create/get DRF auth Token
  ↓
record INVITATION_ACCEPTED audit event
  ↓
commit
```

Use `transaction.atomic()` and lock the invitation row (`select_for_update`) so two browser submissions cannot both consume the same invitation.

The invitation token must remain single-use.

### 5. Authentication after successful registration

The frontend already uses DRF token authentication via:

```text
Authorization: Token <key>
```

Return the newly created auth token in the successful registration response so the frontend can immediately authenticate the teacher without forcing a second login.

Suggested response shape:

```json
{
  "key": "drf-auth-token",
  "user": { ...existing UserSerializer representation... },
  "membership": { ...existing OrganizationMembershipSerializer representation... },
  "detail": "Account created and invitation accepted."
}
```

Use existing serializers for returned objects. Do not create a parallel user representation.

### 6. Do not break the existing authenticated acceptance endpoint

Keep:

```text
POST /api/organizations/{organization_pk}/invitations/accept/
```

for already-authenticated users.

The new registration endpoint is the first-time-account path.

The two paths converge on the same invitation invariants:

- valid pending invitation
- invited email
- no existing membership
- active membership created with invitation role
- invitation becomes accepted
- audit event recorded

Avoid duplicated business rules where practical. A shared service/helper is preferable to copying the same invitation consumption logic into two serializers.

### 7. Existing-account behavior

A teacher who clicks an invitation but already has an account should not be forced through registration.

The new registration endpoint must return a stable error that the frontend can interpret as:

```text
An account already exists for this invitation email. Sign in to continue.
```

The existing authenticated acceptance route remains responsible for accepting the invitation after login.

### 8. Teacher profile

Do **not** invent fake teacher-profile capacity/rate values merely to make registration succeed.

A teacher invitation should create the user as `Role.SUB` and the organization membership as `OrganizationRole.TEACHER`.

Only create `TeacherProfile` here if the repository has a safe existing factory/default path that satisfies its required fields without inventing business data. Otherwise leave profile/configuration provisioning to the existing teacher-admin workflow.

### 9. API schema

Add the new request/response serializers to drf-spectacular automatically.

Regenerate the frontend schema from the backend OpenAPI after the backend endpoint is complete.

Do not hand-edit generated frontend API schema files.

### 10. Tests required

Add backend tests covering at least:

#### Success

- pending teacher invitation + no existing account -> 201
- created user has global role `sub`
- created membership has organization role `teacher`
- membership is active
- invitation is `accepted`
- invitation has `accepted_at`
- returned auth token authenticates the new user
- audit event is recorded

#### Security

- invalid token -> 400/404 according to existing API conventions
- token from another organization URL cannot be used
- expired invitation -> rejected and invitation transitions to `expired`
- revoked invitation -> rejected
- accepted invitation cannot be reused
- second concurrent registration cannot consume the same invitation
- supplied `email` field, if someone tries to add one, is ignored or rejected; invitation email remains authoritative
- supplied global role cannot override `sub`
- supplied organization role cannot override `teacher`

#### Existing account

- invitation email already belongs to a user -> no duplicate user created
- existing account remains unchanged
- response contains a stable error that FE can route to login

#### Password/account validation

- weak password rejected using existing validators
- invalid timezone rejected
- future date of birth rejected using existing rule

### 11. OpenAPI/URL checklist

After implementation verify:

```text
/api/organizations/{organization_pk}/invitations/register/
```

appears in OpenAPI with:

- request schema
- 201 success response
- validation error response
- existing-account response
- no authentication requirement

### 12. Email is separate

Do not block this implementation on SMTP.

The invitation may currently record `email_delivery_status=failed` if SMTP is not configured. The first-time registration endpoint should still work when the raw invitation token is available in a test/dev environment.

## Definition of done

The backend is done only when this exact flow works:

```text
Admin creates teacher invitation
        ↓
Invitation contains organization + raw token
        ↓
Teacher opens invitation URL
        ↓
Teacher has no account
        ↓
POST invitation registration endpoint
        ↓
User(role=sub) created
        ↓
OrganizationMembership(role=teacher, active) created
        ↓
Invitation accepted
        ↓
DRF token returned
        ↓
Teacher is authenticated immediately
```

No `staff` terminology should be introduced anywhere in the implementation, tests, serializer names, API descriptions, or product-facing response messages.
