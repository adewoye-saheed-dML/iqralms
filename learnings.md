# Learnings

Edge cases, gotchas, and decisions discovered while building — so the next
session (or future me) doesn't relearn them the hard way.

Format:

## [date] — short title
- **What happened:**
- **What we decided:**
- **Why it matters for later phases:**

---

## 2026-08-22 — Parent links use a signup code, not the student's email
- **What happened:** The Phase 1 spec left this as an explicit UX call. Email
  lookup would let anyone with a parent account probe whether an arbitrary
  address has a student account, and typos fail silently.
- **What we decided:** Each user gets `User.signup_code` (8 chars, unique,
  generated in `User.save()`, alphabet excludes 0/O/1/I because codes get read
  aloud). The student shares it; the parent POSTs it. This is a field the spec
  didn't list — added with explicit approval, not silently.
- **Why it matters for later phases:** Linking is consent-based, so any future
  "invite" or "family account" flow should build on codes rather than adding
  email lookup back in. The code is only surfaced on the owner's own `/me/`,
  never in another user's payload.

## 2026-08-22 — `is_fully_active` is separate from Django's `is_active`
- **What happened:** The spec wants a minor student to exist but not be
  "complete" until a parent is linked. Reusing Django's `is_active` for that
  would have blocked login entirely, which is heavier than intended.
- **What we decided:** `is_active` keeps its Django meaning (can authenticate).
  `User.is_fully_active` is a computed property: False only for a minor student
  with zero `ParentLink`s. Registration reports
  `status: "pending_parent_link"`.
- **Why it matters for later phases:** Booking must gate on
  `is_fully_active`, not `is_active`. It is computed, so it can't drift — but it
  costs a query per call, so annotate in list views if it ever gets hot.

## 2026-08-22 — `is_minor` is a signup snapshot and goes stale
- **What happened:** The spec says `is_minor` is "computed at signup from
  `date_of_birth`", so it is a stored boolean, not a live calculation. A student
  who signs up at 17 still has `is_minor=True` the day they turn 18.
- **What we decided:** Kept it as spec'd (stored, set by the register
  serializer from `User.minor_from_date_of_birth`, never client-supplied). Did
  not add a recompute job — logged in `tech-debt.md` instead.
- **Why it matters for later phases:** Don't trust `is_minor` for anything
  legally meaningful without recomputing from `date_of_birth` first.

## 2026-08-22 — Cross-table role rules live in `save()`, not DB constraints
- **What happened:** "Only a `parent` can be the parent side of a link", "only
  `lead`/`sub` can hold a `TeacherProfile`" — these read `role` from a *different*
  table, so a `CheckConstraint` can't express them.
- **What we decided:** `ParentLink.save()` and `TeacherProfile.save()` call
  `full_clean()`, so the rules hold for the API, the admin and direct ORM writes
  alike (that's what makes acceptance criterion 5 testable via
  `objects.create`). Duplicate links therefore raise `ValidationError`, **not**
  `IntegrityError` — `full_clean()` checks the unique constraint first.
- **Why it matters for later phases:** Anything catching `IntegrityError` around
  these models will never fire. Also see `tech-debt.md` — this breaks
  `update_fields` and bulk writes.

## 2026-08-22 — `TeacherProfile.is_lead` duplicates `User.role`
- **What happened:** The spec has both `role="lead"` on User and `is_lead` on
  TeacherProfile. Two sources of truth for one fact.
- **What we decided:** Kept both (spec is spec) but validate that they agree —
  `is_lead` must be True exactly when `user.role == "lead"`.
- **Why it matters for later phases:** Routing/payout logic can read either one
  safely. Worth collapsing to one field when the spec is next revised.

## 2026-08-22 — Password hashing made the test suite unusably slow
- **What happened:** 55 tests took 108 seconds. Nearly all of it was PBKDF2
  hashing, because almost every test builds a user.
- **What we decided:** `config/test_runner.py` swaps in MD5 hashing for test
  runs only, wired via `TEST_RUNNER`. Same 55 tests now run in 0.7s.
- **Why it matters for later phases:** Later phases create far more fixture
  data. Keep using `manage.py test` (the runner applies automatically) rather
  than invoking pytest or a custom harness that would miss this.
