# IQRA LMS Backend — Direct Antigravity Correction Playbook

Repository: `adewoye-saheed-dML/iqralms`
Primary stack: Django + Django REST Framework + PostgreSQL
Backend role: **authoritative source for tenancy, authorization, validation, concurrency and business rules**

## 0. WHY THIS FILE EXISTS

This file is an execution document for Antigravity CLI.

It is intentionally written as commands, rules, phases, checks and stop conditions.
Do not turn this into a broad refactor.
Do not implement all phases at once.
Do not mark a phase complete because files exist.
Do not mark a phase complete because a commit message says “complete”.

The frontend must eventually consume the backend through OpenAPI. Therefore:

```text
backend code/tests
        ↓
OpenAPI schema
        ↓
frontend generated client/types
```

The backend is the first authority in this chain.

Source basis:
- Supplied SaaS pre-frontend roadmap: tenant isolation, academy onboarding, enrollment, scheduling, assessment, pricing/payouts, notifications, imports, audit, API contract freeze.
- Supplied frontend master specification: backend remains authoritative for authorization/business rules; OpenAPI is authoritative for endpoint/schema contracts; no invented frontend behavior.

Do not silently change product meaning. When a rule is genuinely unspecified, write `OPEN`, explain the exact missing decision, and stop that phase rather than inventing behavior.

---

# 1. NON-NEGOTIABLE RULES FOR ANTIGRAVITY

1. Work only in `~/projects/iqralms` for this backend repository.
2. Read the existing implementation before editing it.
3. Prefer the smallest change that restores the architecture.
4. Do not rewrite an app just because the existing code is long.
5. Do not create a second authorization system when one already exists.
6. Do not trust organization, role or user identity supplied by a client request body.
7. Tenant context comes from the authenticated request plus the organization in the URL or a canonical tenant-owned object.
8. `OrganizationMembership` answers academy authority.
9. `StudentEnrollment` answers academy academic participation.
10. `User.role` is global account type; `OrganizationMembership.role` is academy authority. Never merge them.
11. `TeacherTrack` is the academy-scoped teaching assignment source of truth.
12. `OrganizationTeacherConfiguration` is the academy-scoped teacher approval/capacity/rate source of truth.
13. Never import a module from `*.tests.*` in production code.
14. Never create a tenant or test object as a runtime fallback in production code.
15. Never guess a user’s academy from “they have only one membership”.
16. Never add frontend-oriented API behavior merely to make a frontend test pass.
17. Every API-affecting change must update OpenAPI and its tests.
18. Preserve PostgreSQL and transaction/concurrency protections.
19. Do not remove historical records merely to simplify state handling.
20. Financial records must not be silently recalculated.
21. If a phase changes a contract, regenerate schema before moving to frontend.
22. A phase is `DONE` only when all its acceptance tests pass.
23. If any required command fails, phase status is `BLOCKED`, not `DONE`.
24. After each phase, update the phase ledger with:
   - what changed
   - files changed
   - tests run
   - exact result
   - unresolved issue, if any
25. Commit only after the phase gate passes.

---

# 2. START HERE — ANTIGRAVITY MASTER PROMPT

Paste this first.

```text
You are working on the Django backend repository:
  ~/projects/iqralms

Goal:
Make the backend internally consistent, tenant-safe, OpenAPI-ready, and safe for the Next.js frontend repository `adewoye-saheed-dML/iqralms_fe`.

STRICT EXECUTION RULES:
1. Do not implement the entire correction in one pass.
2. Work one phase at a time from B00 through B10 in this document.
3. At the start of every phase:
   - inspect the current repository state
   - inspect the exact files named by the phase
   - search for all readers/writers of affected models/functions
   - identify tests already covering the behavior
4. Create a short `implementation_plan.md` for the phase before editing.
5. Do not ask me to repeat requirements already written here.
6. Do not invent missing API behavior.
7. If the API contract is missing information required by a correct implementation, stop and report the exact gap.
8. Never use test factories, test helpers or test-only data creation in production code.
9. Never infer tenant context from number of memberships.
10. Keep `User.role` and `OrganizationMembership.role` separate.
11. Keep `OrganizationMembership` and `StudentEnrollment` separate.
12. Use `TeacherTrack` for academy-scoped teaching assignment.
13. Use `OrganizationTeacherConfiguration` for academy-scoped teacher approval, capacity and payout rate.
14. Preserve existing locking, atomic transactions, validation and historical records.
15. After implementation, run the required commands for the phase.
16. A phase is not complete if a command fails.
17. Do not change `progress.md` to COMPLETE unless the acceptance gate passes.
18. At the end of each phase report:
   - files changed
   - behavior changed
   - tests run
   - exact pass/fail output summary
   - next phase

FIRST ACTION:
Do not edit code yet.
Inspect git status, current HEAD, project tree, CLAUDE.md, README.md, settings, URL routing, model files, permission files, tests, and current OpenAPI schema.
Then create `implementation_plan.md` for B00 only.
Wait for B00 verification before coding B01.
```

---

# 3. BASELINE COMMANDS — B00

Run from WSL:

```bash
cd ~/projects/iqralms

git status --short
git branch --show-current
git log -8 --oneline --decorate

find . -maxdepth 2 -type f | sort | sed -n '1,240p'

python manage.py check
python manage.py showmigrations
python manage.py spectacular --file /tmp/iqralms-schema.yml --validate

rg -n "OrganizationMembership|StudentEnrollment|OrganizationInvitation|active_membership|active_enrollment" accounts organizations curriculum scheduling pricing assessment payouts notifications imports audit_logs
rg -n "OrganizationTeacherConfiguration|TeacherTrack|TeacherProfile\.specialties|hourly_payout_rate|max_weekly_hours|approved" accounts curriculum scheduling payouts
rg -n "tests\.factories|tests/|tests\\." --glob '*.py' .
```

## B00 acceptance gate

B00 is complete only when:

- repository state is known
- current HEAD is known
- Django system check passes
- OpenAPI can be generated/validated
- migrations are understood
- all tenant-sensitive models/readers have been inventoried
- no code is changed merely because an old phase document says it should be changed

Create:

```text
implementation_plan.md
```

Do not edit domain code yet.

---

# 4. B01 — TENANT ACCESS FOUNDATION

## Objective

Make one canonical answer to:

> “Is this user an active member of this academy?”

That answer must also reject inactive organizations.

## Inspect

Primary files:

```text
organizations/models.py
organizations/permissions.py
organizations/views.py
accounts/tenancy.py
```

Search:

```bash
rg -n "def active_membership|OrganizationMembershipQuerySet|organization__is_active|status=.*active|IsOrganizationMember|caller_membership" accounts organizations
```

## Required result

`active_membership(user=..., organization=...)` must:

- reject anonymous users
- match the supplied organization
- require active membership
- require active organization
- return the canonical membership row
- not inspect a client-supplied role
- not guess an organization

All organization membership permission checks should reuse the canonical result.

## Tests that must exist

At minimum:

```text
active member + active org → allowed
active member + inactive org → denied
suspended member → denied
non-member → denied
anonymous → denied
user active in A but not B → denied in B
```

## Commands

```bash
python manage.py test organizations accounts -v 2
python manage.py check
python manage.py spectacular --file /tmp/iqralms-schema.yml --validate
```

## Stop condition

STOP if any endpoint has its own second definition of “active tenant access” that materially differs from the canonical helper.

---

# 5. B02 — MEMBERSHIP VS STUDENT ENROLLMENT

## Objective

Make these two concepts explicit and consistent:

```text
OrganizationMembership = authority inside academy
StudentEnrollment      = academic participation inside academy
```

## Inspect

```text
organizations/models.py
organizations/serializers.py
organizations/views.py
accounts/tenancy.py
accounts/views.py
scheduling/views.py
assessment/views.py
pricing/views.py
payouts/views.py
```

Search all student-access assumptions:

```bash
rg -n "Role\.STUDENT|role.*student|StudentEnrollment|organization_memberships|active_student|active_enrollment|IsStudent|IsStudentOrParent|parent" accounts organizations scheduling assessment pricing payouts
```

## Required backend behavior

A student may have:

- a global `User.role = student`
- an academy `StudentEnrollment`
- academic fields such as track/level/status

Do not make the existence of an admin/staff/teacher membership the definition of student academic participation.

For parent-child visibility, use:

```text
parent has active membership in academy
AND child has active StudentEnrollment in academy
AND ParentLink exists
```

## Critical downstream audit

Check every endpoint that is student-facing.

For each one write down:

```text
Question being answered:
Required relation:
Current relation used:
Correct? yes/no
```

Examples:

```text
Who is this academy’s student?          → StudentEnrollment
Is this student active academically?    → StudentEnrollment.status
What authority does a staff member have? → OrganizationMembership.role
Is this parent inside this academy?      → active OrganizationMembership
Which children may this parent see here? → ParentLink + active StudentEnrollment
```

## Tests

Add regression coverage proving:

```text
child has membership but no enrollment → parent cannot see child here
child enrolled in A but not B         → child not visible through B
inactive enrollment                  → child not visible as active participant
active enrollment                    → visible where parent has active membership
```

Then test all student/parent scheduling, assessment and progress endpoints for the correct relation.

## Commands

```bash
python manage.py test accounts organizations scheduling assessment -v 2
python manage.py check
```

## Stop condition

STOP if there is a real product ambiguity about whether a student-facing operation requires enrollment, membership, or both. Record the exact endpoint and do not invent a rule.

---

# 6. B03 — REAL INVITATION LIFECYCLE

## Objective

Invitation must mean invitation.

Required lifecycle:

```text
pending → accepted
pending → expired
pending → revoked
```

An invitation must NOT grant academy access before acceptance.

## Inspect

```text
organizations/models.py
organizations/serializers.py
organizations/views.py
notifications/services.py
organizations/tests/
```

Search:

```bash
rg -n "OrganizationInvitation|InvitationStatus|generate_token|accepted_at|token_digest|notify_teacher_invitation|memberships/.+POST" organizations notifications
```

## Required model fields

Confirm:

```text
organization
email
role
secure token representation / digest
status
expires_at
accepted_at
created_at
```

Raw invitation tokens must never be persisted.

## Required acceptance rules

1. Token must be valid.
2. Invitation must be pending.
3. Invitation must not be expired.
4. Authenticated user email must match the invitation email if the current product requires authenticated acceptance.
5. Acceptance must be atomic.
6. Membership is created/activated only through the defined acceptance path.
7. An existing membership must have deterministic handling.

### Important current issue to resolve

The existing acceptance path has logic equivalent to:

```python
get_or_create(...)
if not created:
    membership.role = invitation.role
    membership.status = active
```

Do not retain this simply because it “works”. Decide and test a deterministic rule. Preferred safe default unless the product specification says otherwise:

```text
existing membership → reject acceptance with clear machine-readable error
```

Do not silently re-role an existing member by accepting an old invitation.

## Invitation creation

Do not use membership POST as the frontend invitation contract.

Canonical endpoint should remain the invitation endpoint once stabilized.

## Notification

Do not put secrets or passwords in notification payloads.
The invitation token may be included in the invitation delivery mechanism because it is the credential needed to accept, but do not persist the raw token.

## Tests

Must cover:

```text
new email invitation
existing account invitation
duplicate pending invitation
expired invitation
revoked invitation
invalid token
wrong email
accepted invitation
second acceptance
existing membership edge case
invited teacher has no membership before acceptance
accepted teacher gets membership only after acceptance
cross-organization invitation token rejected
```

## Commands

```bash
python manage.py test organizations notifications -v 2
python manage.py check
```

---

# 7. B04 — ELIMINATE TEST-FACTORY RUNTIME FALLBACKS

## Objective

Production code must never create or infer test state.

## Search command

```bash
rg -n "organizations\.tests|scheduling\.tests|tests\.factories|Factory\(" --glob '*.py' .
```

Classify every match:

```text
production source
or
real test source
```

Only real test modules may use factories.

## Mandatory audit

Especially inspect:

```text
scheduling/models.py
scheduling/routing.py
scheduling/views.py
scheduling/serializers.py
pricing/
assessment/
payouts/
```

## Required result

If organization context is missing:

```text
raise a clear validation/error
```

Do NOT:

```text
create OrganizationFactory()
create TeacherFactory()
select “the only organization”
guess from membership count
```

## Commands

```bash
rg -n "tests\.factories|\.tests\.|Factory\(" --glob '*.py' .
python manage.py test scheduling -v 2
python manage.py check
```

Acceptance: no production import/reference remains.

---

# 8. B05 — EXPLICIT ORGANIZATION CONTEXT IN SCHEDULING

## Objective

Every tenant-owned scheduling operation must know exactly which academy it belongs to.

## Inspect

```text
scheduling/models.py
scheduling/views.py
scheduling/serializers.py
scheduling/routing.py
scheduling/urls.py
```

Search:

```bash
rg -n "organization.*None|organization=None|active_membership|single.*membership|membership.*first\(|organization_id|create_from_local|Booking\.objects|Availability\.objects" scheduling
```

## Required result

Preferred route form:

```text
/api/scheduling/organizations/{organization_pk}/...
```

The request organization must be resolved from the URL and caller membership.

For domain objects whose ownership is canonical through another tenant-owned object, derive it from that canonical object only when the relationship is invariant and validated.

## Cross-tenant invariants

Reject combinations such as:

```text
teacher from academy A + level from academy B
availability in A + teacher not active in A
booking in A + level owned by B
cohort in A + level owned by B
waitlist in A + level owned by B
```

## Preserve concurrency

Do not remove:

```text
transaction.atomic()
select_for_update()
TeacherBookingLock
overlap checks
save-time validation
```

## Tests

At minimum:

```text
cross-tenant availability
cross-tenant booking
cross-tenant cohort
cross-tenant waitlist
multiple academy teacher
explicit organization required
no organization guessing
```

## Commands

```bash
python manage.py test scheduling -v 2
python manage.py check
```

---

# 9. B06 — COMPLETE TEACHERTRACK MIGRATION

## Objective

`TeacherTrack` is the academy-scoped source of truth for which teacher may teach which track.

Global `TeacherProfile.specialties` must not remain a runtime authorization fallback.

## Search

```bash
rg -n "specialties|TeacherProfile.*special|\.specialties|TeacherTrack" --glob '*.py' accounts curriculum scheduling assessment pricing payouts
```

## Required result

Runtime teaching eligibility checks must use:

```text
TeacherTrack
+ active teacher membership
+ academy/track alignment
```

Do not fall back to global specialties when organization context is present.

If legacy data migration is required:

1. identify valid legacy assignments
2. migrate only data that can be mapped safely
3. add migration tests
4. make runtime reads use `TeacherTrack`
5. document the legacy field status
6. remove runtime fallback

Do not delete old columns merely to make the search clean unless the migration plan proves they are no longer needed.

---

# 10. B07 — ACADEMY-SCOPED TEACHER CONFIGURATION INCLUDING PAYOUT RATE

## Objective

Use `OrganizationTeacherConfiguration` for:

```text
approved
max_weekly_hours
hourly_payout_rate
```

## Critical audit

Search:

```bash
rg -n "hourly_payout_rate|max_weekly_hours|approved" accounts scheduling payouts curriculum
```

Every academy-scoped reader must use:

```text
OrganizationTeacherConfiguration
```

rather than global:

```text
TeacherProfile.approved
TeacherProfile.max_weekly_hours
TeacherProfile.hourly_payout_rate
```

### Known current defect to fix

`payouts/services.py` currently contains logic equivalent to:

```python
profile = getattr(teacher, "teacher_profile", None)
return profile.hourly_payout_rate
```

This is wrong for multi-academy payouts because the same teacher can have different rates per academy.

Replace the lookup with an academy-specific configuration lookup using the current organization and active teacher membership.

## Tests

Create a teacher who works in two academies:

```text
Academy A → rate A
Academy B → rate B
```

Generate payout in each academy and verify the correct rate is used.

Also test:

```text
unapproved teacher → not bookable
suspended membership → not bookable
capacity from Academy A does not affect Academy B
rate in A does not affect rate in B
```

## Commands

```bash
python manage.py test accounts scheduling payouts -v 2
python manage.py check
```

---

# 11. B08 — PAYOUT TENANCY AND FINANCIAL ISOLATION AUDIT

## Objective

All financial reads/writes must remain academy-scoped and role-scoped.

## Inspect

```text
payouts/models.py
payouts/services.py
payouts/permissions.py
payouts/views.py
pricing/
```

Search:

```bash
rg -n "TeacherPayout|payouts_for|statement_for|organization|teacher_id|LeadPayout|MyPayout" payouts pricing
```

## Required rules

Owner/admin:

```text
may see academy-wide financial records where the endpoint permits it
```

Teacher:

```text
may see own payout records/statements only
```

Student/parent:

```text
no teacher payout access
```

Cross-tenant teacher lookup must be rejected, not silently interpreted as zero.

Payout generation must:

- be academy-scoped
- be deterministic
- remain idempotent
- preserve finalized records
- retain race protection
- not reprice historical payouts

## Tests

Two academies with the same teacher.
Different rates.
Different bookings.
Run generation separately.
Verify no cross-tenant records appear.

---

# 12. B09 — OPENAPI AS THE FRONTEND CONTRACT

## Objective

The schema must describe the backend actually implemented.

## Generate

Run:

```bash
python manage.py spectacular --file schema.yml --validate
```

If the repository uses a different schema path, inspect `config/urls.py` and `SPECTACULAR_SETTINGS` first.

## Verify these endpoints

```text
GET  /api/accounts/me/
GET  /api/accounts/my-children/
POST /api/accounts/parent-links/
GET  /api/accounts/organizations/{organization_pk}/children/
GET/POST/PATCH teacher configurations
GET  /api/organizations/mine/
POST /api/organizations/
GET  /api/organizations/{organization_pk}/
GET  /api/organizations/{organization_pk}/memberships/
PATCH /api/organizations/{organization_pk}/memberships/{id}/
GET/POST /api/organizations/{organization_pk}/students/
GET/PATCH /api/organizations/{organization_pk}/students/{id}/
GET/POST /api/organizations/{organization_pk}/invitations/
POST /api/organizations/{organization_pk}/invitations/accept/
organization-scoped scheduling endpoints
organization-scoped pricing endpoints
organization-scoped assessment endpoints
organization-scoped payout endpoints
organization-scoped notification endpoints
organization-scoped import endpoints
organization-scoped audit endpoints
```

## Critical schema requirement

GET endpoints should expose real response schemas, not only:

```yaml
description: List of students
```

For the student list/detail response, the frontend must be able to generate a useful type without inventing one.

Do the same for invitation responses.

## No undocumented frontend transport

After the backend schema is regenerated, the frontend must be able to call the contract without `@ts-expect-error` workarounds.

## Commands

```bash
python manage.py spectacular --file schema.yml --validate
python manage.py check
```

Then inspect:

```bash
rg -n "organizations_students|organizations_invitations|OrganizationInvitation|StudentList|StudentDetail" schema.yml
```

---

# 13. B10 — FINAL BACKEND VERIFICATION

## Run the full relevant suite

First determine the repository's supported test command.

Examples:

```bash
python manage.py test -v 2
```

or the documented pytest command if the repository uses pytest.

Also run:

```bash
python manage.py check
python manage.py spectacular --file /tmp/iqralms-final-schema.yml --validate
```

## Required regression categories

### Tenant isolation

```text
organization A cannot read organization B
organization A cannot mutate organization B
inactive organization is inaccessible
suspended member is inaccessible
```

### Roles

```text
global User.role is not treated as academy role
academy OrganizationMembership.role is scoped per academy
```

### Enrollment

```text
membership != enrollment
parent visibility uses enrollment
cross-tenant enrollment impossible
```

### Invitations

```text
pending does not grant access
acceptance grants membership
expired/revoked cannot grant access
existing membership handling deterministic
```

### Scheduling

```text
explicit org
no test factory fallback
TeacherTrack authorization
academy-specific teacher config
concurrency preserved
```

### Finance

```text
academy-specific rate
academy-specific records
teacher sees own records only
owner/admin see permitted academy records
```

### OpenAPI

```text
schema validates
responses have usable schemas
paths match implementation
frontend does not need undocumented endpoints
```

---

# 14. FILES THAT MUST NOT BE LEFT STALE

After the final phase, inspect at least:

```text
CLAUDE.md
README.md
learnings.md
tech-debt.md if present
schema.yml
phase/progress trackers if present
```

The current root `CLAUDE.md` was observed to still identify the old `quran_acad` repository and old phase/head information. That must be corrected.

The active repository is:

```text
adewoye-saheed-dML/iqralms
```

Do not claim the repository documentation is current until it actually is.

---

# 15. PHASE LEDGER FORMAT

Maintain a simple file:

```text
CORRECTION_PROGRESS.md
```

Use:

```markdown
# IQRA LMS Backend Correction Progress

| Phase | Status | Evidence |
|---|---|---|
| B00 | DONE/BLOCKED | exact commands + result |
| B01 | DONE/BLOCKED | exact commands + result |
| B02 | DONE/BLOCKED | exact commands + result |
| B03 | DONE/BLOCKED | exact commands + result |
| B04 | DONE/BLOCKED | exact commands + result |
| B05 | DONE/BLOCKED | exact commands + result |
| B06 | DONE/BLOCKED | exact commands + result |
| B07 | DONE/BLOCKED | exact commands + result |
| B08 | DONE/BLOCKED | exact commands + result |
| B09 | DONE/BLOCKED | exact commands + result |
| B10 | DONE/BLOCKED | exact commands + result |
```

Never use `COMPLETE` when the evidence is a commit message only.

---

# 16. FINAL BACKEND STOP CONDITIONS

Do not move to new product work when any of these is true:

```text
❌ production imports test factories
❌ tenant access is guessed
❌ inactive academy still passes tenant access
❌ membership and enrollment meanings are mixed
❌ parent visibility ignores StudentEnrollment
❌ invitation creates access before acceptance
❌ invitation acceptance silently re-roles an existing member without an explicit rule
❌ scheduling can operate without explicit or canonical tenant context
❌ TeacherProfile.specialties is a runtime authorization fallback
❌ payout rate is globally read from TeacherProfile
❌ OpenAPI response types are missing for frontend-critical endpoints
❌ frontend requires an undocumented POST/PATCH endpoint
❌ CLAUDE.md identifies the wrong repository/current phase
❌ any required test command fails
❌ OpenAPI validation fails
```

When all are clear and the full regression suite passes, the backend is ready for the frontend correction sequence.
