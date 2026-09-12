# SaaS Phase 10 — Bulk Import

## Status

READY FOR IMPLEMENTATION

## Repository

`adewoye-saheed-dML/quran_acad`

## Target branch

`main`

## Current baseline

The repository has completed SaaS Phases 1–9.

Current completed phase:

**SaaS Phase 9 — Video Provider Abstraction**

The live repository's current Phase 9 completion commit is:

`9bb62eea4ccd40e2c462527ae32f25d586a4911e`

The repository's `CLAUDE.md` explicitly identifies **SaaS Phase 10** as the next implementation phase.

Phase 9 established the provider-neutral video boundary, including academy-scoped video-provider configuration and provider-neutral meeting fields.

---

# 1. Objective

Add a tenant-safe bulk import capability for academy staff so an academy can migrate existing teachers, students and parents from CSV/XLSX files without developer intervention.

The import system must prioritize:

- validation before mutation;
- deterministic academy ownership;
- understandable row-level errors;
- repeat-safe behaviour;
- explicit mapping;
- safe handling of existing users;
- auditable import results.

The first version should be an API/backend workflow. Do not build a large frontend spreadsheet editor in this phase.

---

# 2. Why this phase exists

The SaaS roadmap identifies bulk CSV/XLSX import as the next practical commercial feature after notification and video-provider boundaries.

A real academy will often have existing records in spreadsheets. Requiring manual record creation would make onboarding slow and developer-dependent.

The product therefore needs a controlled workflow:

```text
upload file
    ↓
inspect and validate
    ↓
show row-level errors
    ↓
confirm import
    ↓
create/update academy records
    ↓
return import summary
```

The important boundary is that a failed row must never silently create partial or cross-academy data.

---

# 3. Scope

## In scope

- CSV import.
- XLSX import.
- Tenant-scoped import jobs.
- File metadata and validation result storage.
- Header/column mapping.
- Row-level validation errors.
- Dry-run/preview behaviour before committing changes.
- Transaction-safe import execution.
- Deterministic user matching.
- Safe creation of academy memberships.
- Student/parent relationship handling using the repository's existing identity model.
- Optional student curriculum/enrollment fields only where the current repository already has the required authoritative model.
- Import result summaries.
- Idempotent/repeat-safe behaviour.
- Import permissions for academy owner/admin/staff according to the existing organization-role model.
- Security and tenant-isolation tests.
- PostgreSQL phase gate.
- API/OpenAPI documentation.
- Documentation of actual decisions and deliberate deferrals.

## Explicitly out of scope

- Full frontend spreadsheet editor.
- Background worker/queue infrastructure.
- Automatic invitation delivery through email/WhatsApp/Telegram.
- New membership lifecycle states such as `pending` or `invited`.
- Payment import/reconciliation.
- Historical assessment import unless the existing schema already provides a clearly safe import contract.
- Historical payout import.
- Complex migration scripting for external databases.
- Per-academy custom import programming.
- AI-assisted column mapping.
- Marketing automation.

---

# 4. Existing architecture constraints

## 4.1 Global User identity

`accounts.User` is the global identity model.

Do not add:

```text
User.organization
User.academy
```

A user may belong to multiple academies.

Imports must therefore treat:

```text
User
OrganizationMembership
```

as separate concepts.

## 4.2 Organization membership

Academy access is represented by `OrganizationMembership`.

The current membership states are:

```text
active
suspended
```

Do not invent `pending` or `invited` membership states in Phase 10.

Invitation/onboarding workflow is a separate later decision.

## 4.3 ParentLink

`ParentLink` represents the real-world parent/child relationship and is global.

The import system must not create a parent-child relationship merely because two rows share an academy.

Only create a `ParentLink` when the import provides enough explicit information to identify the parent and student safely.

## 4.4 Organization teacher configuration

Teacher-specific academy behaviour belongs to the existing organization-scoped teacher configuration where applicable.

Do not move those fields onto `User` as part of import.

## 4.5 Existing SaaS boundary

Every import request must operate inside exactly one selected academy context.

A client-supplied organization ID is not trusted by itself.

---

# 5. Import job model

Create a tenant-owned `ImportJob` or equivalent central model.

Use the smallest safe field set.

Conceptually:

```text
id
organization
created_by
kind
file_name
file_size
file_type
status
column_mapping
row_count
valid_row_count
invalid_row_count
created_count
updated_count
skipped_count
error_count
error_report
created_at
started_at
completed_at
```

Possible status values:

```text
uploaded
validated
failed
ready
running
completed
partially_completed
```

Exact names may follow repository conventions.

## Ownership

The import job must have deterministic academy ownership.

An owner/admin/staff member may see only import jobs belonging to their academy and only where their role permits import operations.

A user from Academy A must not read an import job from Academy B by known ID.

---

# 6. File handling

Accept only:

```text
CSV
XLSX
```

Reject:

- executable files;
- unsupported spreadsheet formats;
- arbitrary archive uploads;
- files whose declared type conflicts with content where the existing upload-validation architecture can detect it.

The implementation must impose sensible limits for:

```text
maximum file size
maximum row count
maximum cell length
maximum number of columns
```

Do not create a giant-memory import path.

Where practical:

- validate the upload before parsing;
- avoid reading the same file repeatedly;
- keep error output bounded.

The project already treats private uploaded media carefully. Import files should follow the same security principle and should not become public static/media resources.

---

# 7. Import kinds

The first implementation should support explicit import kinds rather than trying to infer the entire dataset automatically.

Recommended:

```text
teachers
students
parents
```

A combined family/student import may be supported only if the row contract remains unambiguous.

Do not create an unrestricted "import anything" endpoint.

---

# 8. Canonical row contracts

The import system must define documented canonical fields.

## Teacher row

Minimum useful fields should include only what the current User and academy teacher models actually require, for example:

```text
email
first_name
last_name
timezone
```

Optional fields may include existing teacher-specific fields when they map cleanly to the current schema.

Do not invent import columns for unsupported business data.

## Student row

Conceptually:

```text
email
first_name
last_name
timezone
date_of_birth
parent_email
```

Only fields that map safely to current models should be implemented.

## Parent row

Conceptually:

```text
email
first_name
last_name
timezone
```

A parent relationship should not be guessed.

The exact documented field list should be finalized from the actual serializers/models during Task 10.1.

---

# 9. Column mapping

Do not require every academy's spreadsheet to use the exact canonical header names.

Support a mapping step:

```text
incoming header
        ↓
canonical field
```

Example:

```text
"Parent Email"  → parent_email
"First Name"    → first_name
"Given Name"    → first_name
```

The server must validate that each target canonical field is legal for the chosen import kind.

Do not permit:

```text
organization_id
membership_id
role
tenant_id
```

to be accepted as unrestricted authority fields from the spreadsheet.

The academy is determined by the authenticated request context.

---

# 10. Validation before mutation

The normal workflow should be:

```text
upload
→ parse
→ map headers
→ validate all rows
→ produce report
→ confirm
→ execute import
```

The system should not create users or memberships merely because a file was uploaded.

Validation should detect at least:

- missing required fields;
- invalid email formats;
- invalid timezone values;
- invalid dates;
- unsupported role values;
- duplicate rows within the file;
- conflicting rows within the file;
- invalid parent references;
- ambiguous existing-user matches;
- attempts to link records across academies;
- invalid organization-scoped teacher data;
- malformed spreadsheet structure.

---

# 11. Deterministic user matching

Existing-user matching is one of the highest-risk parts of bulk import.

Use a documented matching strategy.

The preferred first key is:

```text
normalized email
```

Do not silently match on:

```text
first name
last name
phone number
```

unless a later explicitly approved rule exists.

Email normalization should follow one documented repository rule.

Example:

```text
USER@Example.COM
user@example.com
```

should resolve consistently.

If multiple existing records make a match ambiguous:

```text
do not guess
mark the row invalid
```

---

# 12. Academy membership behaviour

For an existing global user:

```text
existing User
    ↓
check OrganizationMembership
    ↓
create or reuse membership
```

The import must not duplicate an existing membership.

If a user is already an active member of the academy with the expected role:

```text
reuse
```

If the user is suspended:

The import must follow an explicit rule. Recommended default:

```text
do not silently reactivate
report the row as requiring review
```

Do not turn import into a hidden account-reactivation mechanism.

If an existing user is already a member with a conflicting role:

```text
do not overwrite automatically
report a role conflict
```

Role changes are a distinct authorization decision.

---

# 13. New user creation

When no safe existing-user match exists:

Create the User only with the fields required by the selected import kind.

Do not create credentials from spreadsheet data.

Do not accept passwords from imports.

Do not store temporary passwords in:

```text
ImportJob
error reports
logs
notification payloads
```

Account activation/invitation delivery is outside Phase 10 unless the current repository already has a safe and established mechanism.

A newly imported user may therefore exist as an account/membership record without an automatic invitation workflow.

Document this clearly in the import result.

---

# 14. Parent-child relationship import

A parent-child relationship must be explicit.

Recommended mechanism:

```text
student row contains parent_email
```

or:

```text
parent row contains child_email
```

The direction may be chosen based on the actual API/data model.

Required safety:

```text
resolved parent
resolved student
both valid User records
both belong to the selected academy through valid memberships
```

Only then create `ParentLink`.

Never create a parent link solely because:

```text
surname matches
names match
rows are adjacent
```

If a relationship is ambiguous:

```text
row fails validation
no relationship is created
```

---

# 15. Transaction model

The system should distinguish:

## Preview/validation transaction

No user or academy records are mutated.

## Commit transaction

The confirmed import should use transactional boundaries appropriate to the repository.

Prefer all-or-nothing behaviour for a small import operation unless the actual implementation proves that bounded per-row commits are necessary.

If partial success is deliberately supported, the implementation must make it explicit and return precise row-level results.

Do not claim an import is successful if some requested records failed without reporting them.

---

# 16. Idempotency

Repeated import of the same valid data should not create duplicate users or memberships.

Example:

```text
first import:
10 valid rows
→ 10 created

same file imported again:
→ 0 duplicate users
→ existing memberships reused
```

The exact update semantics must be explicit.

Recommended first release:

- create missing users;
- reuse matching users;
- create missing academy memberships;
- do not overwrite unrelated existing profile values automatically;
- report fields that differ but were not changed.

Do not silently replace manually maintained data with spreadsheet values.

---

# 17. Dry-run and commit API

Prefer a two-stage API contract.

Conceptually:

```text
POST /api/imports/organizations/<organization_pk>/validate/
```

returns:

```text
import job id
status
row counts
column mapping
validation errors
warnings
```

Then:

```text
POST /api/imports/organizations/<organization_pk>/<import_id>/commit/
```

executes the validated import.

Exact names may follow repository conventions.

The server must verify:

- the import job belongs to the selected academy;
- the caller still has permission;
- the import is in a committable state;
- the file/result has not been tampered with;
- the import has not already been completed.

---

# 18. Error reporting

Every invalid row should be explainable.

Conceptually:

```json
{
  "row": 17,
  "field": "email",
  "code": "invalid_email",
  "message": "Email address is not valid."
}
```

Error records should be structured rather than a single opaque text blob.

Where useful, expose:

```text
row number
field
error code
human message
```

Keep messages safe and avoid leaking:

- database internals;
- secrets;
- filesystem paths;
- SQL;
- provider credentials.

---

# 19. Import result

The API should return a summary such as:

```text
total rows
valid rows
invalid rows
created users
reused users
created memberships
reused memberships
created parent links
skipped rows
failed rows
```

For committed imports, retain enough result information for academy administrators to understand what happened later.

Do not retain unnecessary copies of sensitive source data indefinitely.

---

# 20. Permissions

Use existing organization membership and role infrastructure.

Import authority should initially be limited to:

```text
Owner
Admin
Staff
```

Teacher/student/parent should not have bulk-import authority unless a concrete existing repository policy already says otherwise.

A suspended membership must not use import endpoints.

A user must never import into an academy they do not belong to.

Do not create a second permission system solely for imports.

---

# 21. Tenant isolation

Required invariants:

1. Academy A cannot read Academy B import jobs.
2. Academy A cannot validate/commit Academy B import jobs.
3. A spreadsheet cannot assign rows to another academy through organization identifiers.
4. Existing users from other academies must not have their unrelated memberships modified.
5. A parent link cannot be created using users belonging only to another academy.
6. Teacher academy configuration must not cross tenants.
7. Known import-job IDs cannot bypass tenant checks.
8. Suspended/non-members cannot use import operations.
9. A malformed row must not create cross-tenant relationships.
10. Commit must re-check tenant context rather than trusting validation-time context alone.

Tenant filtering belongs in:

```text
querysets/services
permissions
model/service validation
tests
```

not only serializers.

---

# 22. Concurrency and repeat submission

Two commit requests for the same import must not create duplicate users or memberships.

Use database constraints and/or transactional locking where appropriate.

The implementation should prove:

```text
same import committed twice concurrently
→ one logical import result
→ no duplicate membership rows
→ no duplicate user creation caused by the race
```

Do not rely only on an in-memory lock.

---

# 23. Security

Treat import files as untrusted input.

Protect against:

- oversized files;
- formula injection in generated error/export files, if any are added;
- malformed XLSX structures;
- malicious values;
- huge cell values;
- unexpected encodings;
- duplicate headers;
- hidden columns being mistaken for authorized data;
- arbitrary organization identifiers;
- credentials/passwords embedded in rows.

Do not add support for exporting raw imported passwords because passwords are not accepted.

---

# 24. Testing requirements

## File tests

Prove:

- valid CSV accepted;
- valid XLSX accepted;
- unsupported formats rejected;
- oversized files rejected;
- malformed files rejected.

## Mapping tests

Prove:

- canonical headers map correctly;
- supported aliases map correctly;
- duplicate canonical mappings are rejected;
- forbidden authority fields are rejected.

## Validation tests

Cover:

- invalid email;
- invalid timezone;
- invalid date;
- missing required field;
- duplicate row;
- conflicting row;
- ambiguous user match;
- parent reference failure;
- teacher role/configuration conflict.

## Tenant isolation tests

Prove:

```text
Academy A admin cannot access Academy B import job.
Known job id from B is rejected.
Commit cannot switch organization.
Cross-academy parent link is rejected.
Cross-academy teacher membership is rejected.
```

## Idempotency tests

Prove:

```text
same data twice
→ no duplicate users

same data twice
→ no duplicate memberships

same import committed twice
→ second commit is rejected or safely treated as already completed
```

## Concurrency tests

Prove that simultaneous commits do not create duplicates.

## Regression tests

All existing:

```text
accounts
curriculum
scheduling
pricing
assessment
payouts
notifications
video-provider
```

tests must remain green.

---

# 25. PostgreSQL phase gate

Run against PostgreSQL:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Also verify:

```text
OpenAPI documentation
tenant isolation
manual two-academy acceptance
repeat import behaviour
```

---

# 26. Manual acceptance

## Academy A

```text
Owner/Admin uploads CSV
→ headers mapped
→ validation report shown
→ invalid rows identified
→ valid rows remain usable
→ commit creates/reuses records
→ import summary is stored
```

## Existing user

```text
Existing global user
→ matching email detected
→ no duplicate User created
→ Academy A membership created or reused
```

## Existing conflicting user

```text
Existing user in another academy / conflicting role
→ no unrelated tenant record modified
→ conflict reported
```

## Parent relationship

```text
Valid parent + valid student + explicit relationship
→ ParentLink created

Ambiguous relationship
→ no ParentLink created
→ row reported as invalid
```

## Repeat import

```text
Import the same file again
→ no duplicate users or memberships
```

## Cross-tenant attack

```text
Change organization id in request/body
→ request cannot move the import into another academy
```

---

# 27. Recommended task breakdown

## Task 10.1 — Audit and canonical import contracts

Inspect the actual current models/serializers and define the canonical row schemas for:

```text
teachers
students
parents
```

Do not implement unsupported fields.

## Task 10.2 — ImportJob model and file handling

Create the tenant-scoped import-job record and safe file-validation boundary.

## Task 10.3 — Parser and column mapping

Implement CSV/XLSX parsing and explicit canonical field mapping.

## Task 10.4 — Validation engine

Create structured row-level validation errors and warnings without mutating domain data.

## Task 10.5 — User/membership matching

Implement deterministic email-based matching and academy membership creation/reuse.

## Task 10.6 — Parent-link handling

Implement only explicit, safely resolvable parent/student relationships.

## Task 10.7 — Commit service

Implement transaction-safe creation/reuse behaviour and idempotent commit semantics.

## Task 10.8 — Tenant/security tests

Add adversarial cross-academy tests, duplicate tests and concurrency tests.

## Task 10.9 — API and OpenAPI

Expose the validate/commit workflow using existing organization-scoped routing and permission conventions.

## Task 10.10 — Phase gate and documentation

Run the PostgreSQL gate, manual acceptance, update documentation, and create one coherent phase-completion commit.

---

# 28. Definition of done

SaaS Phase 10 is complete only when:

1. CSV import works for the explicitly supported import kinds.
2. XLSX import works for the explicitly supported import kinds.
3. Import jobs are academy-scoped.
4. File parsing occurs before domain mutation.
5. Column mapping is explicit and validated.
6. Row-level errors are structured and understandable.
7. Existing users are matched deterministically.
8. Duplicate academy memberships are not created.
9. Parent-child links are created only when explicitly and safely resolvable.
10. The commit operation is transactional or otherwise explicitly partial-safe.
11. Repeated imports do not create duplicate users/memberships.
12. Cross-academy attacks are rejected.
13. Suspended/non-members cannot use import endpoints.
14. Import permissions use existing organization roles.
15. PostgreSQL checks/migrations/tests pass.
16. Existing domain regressions remain green.
17. OpenAPI reflects the new import API.
18. Documentation records actual decisions and deliberate deferrals.
19. A coherent git commit marks SaaS Phase 10 complete.

---

# 29. Stop instead of guessing

Stop before implementation when:

- the current domain model does not contain a safe target for an import field;
- matching an existing user cannot be deterministic;
- a parent-child relationship is ambiguous;
- a requested field would change tenant authority;
- importing a record would require a new membership lifecycle state;
- account activation/invitation becomes necessary to complete the workflow;
- partial import semantics are required but not explicitly defined;
- the request expands into payment, assessment, payout or marketing migration.

Do not invent missing onboarding or membership states merely to make a spreadsheet import appear complete.

---

# 30. Phase boundary

Phase 10 establishes the backend bulk-import boundary for academy onboarding.

Later phases may build on it for:

- richer onboarding UI;
- invitation delivery;
- audit logging;
- more advanced imports;
- API contract freeze;
- frontend workflows.

Those should not be pulled into Phase 10 without a concrete dependency.
