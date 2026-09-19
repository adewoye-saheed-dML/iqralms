# IQRA LMS Backend — Current Correction Specification

Repository: `adewoye-saheed-dML/iqralms`
Branch: `main`

## Purpose

This is the single current backend correction instruction for the defects verified in the repository.

Work only in `iqralms`.

---

## 1. Rules

- Inspect the current code before editing.
- Do not modify `iqralms_fe`.
- Do not rewrite unrelated apps.
- Preserve existing tenant isolation.
- Do not create duplicate models or permission systems.
- Add regression tests for every behaviour changed.
- Do not claim a phase is complete without passing verification.
- Do not use SQLite to hide PostgreSQL-specific failures.
- Do not hand-edit generated OpenAPI output when the generator should produce it.
- Remove temporary working files when the correction is finished.

---

# 2. Fix academy-specific payout rates

### Verified defect

`payouts/services.py` still resolves the payout rate from the global teacher profile:

```python
teacher.teacher_profile.hourly_payout_rate
```

The academy-specific source is:

```text
OrganizationTeacherConfiguration.hourly_payout_rate
```

### Required change

Make payout rate resolution explicitly depend on:

```text
organization + teacher
        ↓
active organization membership
        ↓
OrganizationTeacherConfiguration
        ↓
hourly_payout_rate
```

Do not use the global `TeacherProfile.hourly_payout_rate` as a fallback for an academy-scoped payout.

Preserve the existing `no_payout_rate` behaviour when the academy has no configured rate.

### Required regression test

Use the same teacher in two academies:

```text
Academy A = 5000/hour
Academy B = 8000/hour
```

Generate payouts in both academies and prove that each uses its own rate.

Also test:

- changing Academy A's rate does not affect Academy B;
- missing academy rate produces the expected no-rate behaviour.

---

# 3. Fix invitation acceptance

### Verified defect

`OrganizationInvitationAcceptSerializer.save()` currently uses `get_or_create()` and can silently:

- change an existing member's role;
- reactivate an existing suspended member.

### Required behaviour

If the accepting user already has a membership in the organization:

- reject the invitation;
- do not change the role;
- do not reactivate the membership;
- do not mark the invitation accepted.

Return a normal DRF validation error using the project's existing error style.

### Atomicity

Wrap acceptance in one transaction:

```text
validate invitation
        ↓
create membership
        ↓
mark invitation accepted
```

If any operation fails, none of the state changes may remain committed.

### Tests

Add tests for:

1. valid invitation creates membership and is accepted;
2. existing active membership is rejected;
3. existing suspended membership is rejected;
4. existing role remains unchanged;
5. suspended status remains unchanged;
6. rejected invitation remains pending;
7. transaction failure rolls back;
8. owner cannot be created through an invitation.

---

# 4. Make student participation semantics consistent

### Verified inconsistency

`StudentEnrollment` now represents academy-specific student participation, but some scheduling paths still use organization membership directly.

Inspect:

```text
booking creation
student booking lists
routing
parent booking flow
pricing access
assessment access
progress access
student/parent permission helpers
```

### Required change

Determine the intended rule from the current backend models, specs and tests.

Then use one canonical semantic distinction:

```text
OrganizationMembership
    = authority/access to the academy

StudentEnrollment
    = academic participation in the academy
```

If an endpoint genuinely requires both, document that in the permission/test logic.

Do not leave equivalent student-participation operations using different rules.

### Required tests

Prove:

- active enrollment works according to the chosen rule;
- inactive enrollment does not count as active participation;
- cross-academy enrollment cannot grant access;
- parent access still requires the parent's academy access plus valid child participation;
- cross-tenant IDs remain isolated.

---

# 5. Remove incorrect global teacher fallbacks

### Verified inconsistency

Academy scheduling still has paths that consult global `TeacherProfile` values for:

- teacher approval;
- weekly capacity.

The academy-specific configuration contains:

```text
OrganizationTeacherConfiguration.approved
OrganizationTeacherConfiguration.max_weekly_hours
OrganizationTeacherConfiguration.hourly_payout_rate
```

### Required change

For academy-scoped decisions, use the academy configuration.

Use `TeacherProfile` only for genuinely global/person-level information.

Do not delete legacy database fields unless the existing architecture explicitly requires their removal.

### Regression tests

Test:

```text
TeacherProfile.approved = False
Academy configuration.approved = True
```

and verify the authoritative academy rule.

Also configure different weekly limits for the same teacher in two academies and verify each academy enforces its own limit.

---

# 6. Resolve the dead membership-create implementation

The repository contains `OrganizationMembershipCreateSerializer`, while the membership collection view is currently list-only.

Choose one explicit architecture based on the current product specification.

### If invitations are the canonical onboarding path

Remove the unused direct membership-create serializer/code.

Keep membership listing/detail/update behaviour as required.

### If direct admission is still required

Implement POST properly:

- wire the serializer into the view;
- enforce academy permissions;
- add OpenAPI request/response schemas;
- add tests.

Do not leave a serializer that implies an API operation that does not exist.

---

# 7. Fix the student OpenAPI contract

Current student endpoints have successful responses documented only with descriptions instead of concrete response schemas.

Fix the schema annotations so the generated OpenAPI contract describes the real serializers.

At minimum:

```text
GET collection
    → student enrollment list serializer

POST collection
    → created student enrollment serializer

GET detail
    → student enrollment detail serializer

PATCH detail
    → student enrollment detail serializer
```

Regenerate `schema.yml` using the repository's normal generation command.

Do not manually maintain a duplicate frontend API contract.

---

# 8. Fix `/organizations/mine/`

The endpoint must follow the same canonical active-organization rule as `active_membership()`.

An inactive organization must not appear as an actively usable academy.

Add a regression test.

---

# 9. Update stale backend documentation

The root `CLAUDE.md` is stale and still refers to:

```text
adewoye-saheed-dML/quran_acad
```

Update it to:

```text
adewoye-saheed-dML/iqralms
```

Use the actual current architecture.

Do not create another phase-specific Claude file just for this correction.

---

# 10. Fix payout test fixtures

Critical payout tests must create:

```text
OrganizationTeacherConfiguration.hourly_payout_rate
```

rather than relying on:

```text
TeacherProfile.hourly_payout_rate
```

Global teacher-profile fixtures should remain only where they test genuinely global profile behaviour.

---

# 11. Search for remaining incorrect global fallbacks

After implementation, search for:

```bash
rg -n "hourly_payout_rate|max_weekly_hours|approved|TeacherProfile|OrganizationTeacherConfiguration|TeacherTrack|active_membership|StudentEnrollment" .
```

Classify each relevant match as:

```text
GLOBAL / PERSON-LEVEL
ACADEMY-SCOPED
LEGACY DATA
TEST FIXTURE
DEAD CODE
```

Any academy-scoped operation still depending on a global teacher setting must be corrected.

---

# 12. Verification

Run:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py spectacular --file schema.yml --validate
python manage.py test
python manage.py check --deploy --fail-level WARNING
```

Also run the relevant app suites individually if the repository supports them:

```bash
python manage.py test organizations
python manage.py test accounts
python manage.py test scheduling
python manage.py test payouts
```

Use PostgreSQL for the test environment.

If a command cannot run because of the local environment, report the exact limitation. Do not claim it passed.

---

# 13. Completion gate

Do not mark the correction complete until:

- [ ] academy-specific payout rate is authoritative;
- [ ] multi-academy payout regression passes;
- [ ] invitation acceptance is atomic;
- [ ] existing-member invitation acceptance is rejected;
- [ ] suspended members are never reactivated by invitation;
- [ ] student participation semantics are consistent;
- [ ] academy approval is authoritative;
- [ ] academy capacity is authoritative;
- [ ] membership-create code is either removed or fully implemented;
- [ ] student OpenAPI responses have concrete schemas;
- [ ] `/organizations/mine/` excludes inactive organizations;
- [ ] root `CLAUDE.md` is current;
- [ ] payout fixtures test academy-specific configuration;
- [ ] global fallback search finds no incorrect academy-scoped dependency;
- [ ] Django checks pass;
- [ ] migrations check passes;
- [ ] OpenAPI validation passes;
- [ ] full test suite passes;
- [ ] deployment check passes, or its environment limitation is explicitly reported.

## Final report

Return only:

### Changed
Exact files and what was fixed.

### Tests
Exact commands and results.

### Remaining
Only genuine unresolved issues.
