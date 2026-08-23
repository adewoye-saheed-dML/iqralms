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

## 2026-08-23 — Placement review is lead-only (the spec's open question)
- **What happened:** `specs/phase-2-curriculum.md` deliberately refused to
  decide whether "you or a senior teacher" meant the lead alone or approved
  sub-teachers too, and said to ask rather than default.
- **What we decided:** **Lead only.** `role == "lead"` is the gate in
  `curriculum/permissions.py::IsLeadTeacher` (both `/placements/pending/` and
  `/placements/{id}/review/`) and again in `PlacementResult.clean()`, which
  rejects any `reviewed_by` that isn't a lead. A sub-teacher gets 403 even when
  their `TeacherProfile.approved` is True.
- **Why it matters for later phases:** Placement is now a lead bottleneck by
  design, which is the *opposite* of what Phase 4 routing is for — if the queue
  becomes the pinch point, the fix is to widen this gate to approved subs
  (`TeacherProfile.approved` already exists for exactly this), not to route
  around it. Two places change plus the tests that assert sub → 403.

## 2026-08-23 — `PlacementResult.status` is stored, not derived
- **What happened:** The spec preferred deriving `status` from whether
  `reviewed_by` is set, "unless that makes the queryset annoying." It does, and
  worse: `reviewed_by` is *deliberately null* for a beginner skip, which is
  nonetheless `reviewed`. So `reviewed_by` cannot be the signal at all — a
  derived-from-`reviewed_by` status would report every auto-placed beginner as
  still pending.
- **What we decided:** Store `status`, but never let a client or the admin set
  it: `save()` recomputes it from `reviewed_at` on every write (`readonly_fields`
  in the admin). `reviewed_at` is the real source of truth, and `clean()` refuses
  a row where `recommended_level` and `reviewed_at` disagree, so the stored value
  cannot drift. The lead's review queue is then a plain
  `filter(status="pending")` instead of a null-check on a nullable FK.
- **Why it matters for later phases:** Read `status`, don't recompute it. If a
  third state ever appears (`needs_resubmission`, say), it must be derivable
  from stamps in `save()` too — don't start setting `status` from a view.

## 2026-08-23 — "No gaps in `Level.order`" is enforced as append-only
- **What happened:** The spec wants gap-free, duplicate-free `order` values
  "enforced at save time, not just by convention." Duplicates are a
  `UniqueConstraint`, but gaps read *sibling rows*, so no DB constraint can
  express them.
- **What we decided:** `Level.clean()` allows exactly one order for a new level
  — `max(order) + 1` for that track, via `Level.next_order_for()` — and makes an
  existing level's `order` immutable. Levels are therefore appended, never
  inserted or renumbered. `LevelFactory` derives `order` from
  `next_order_for()` for the same reason; a hardcoded order breaks on the second
  level in a track.
- **Why it matters for later phases:** Inserting a level in the middle of a
  ladder is not currently possible through the ORM or admin — it needs a
  deliberate renumbering operation (and a decision about what happens to
  placements already pointing at the levels being shifted). Don't add it
  casually; `recommended_level` is `PROTECT`ed for the same reason.

## 2026-08-23 — Re-submitting a placement orphans the old audio file
- **What happened:** The spec's "a second placement request updates the existing
  row" rule is implemented by reassigning `audio_sample` in
  `PlacementResult.submit()`. Django has not deleted the displaced file since
  1.3, so the old sample stays in `MEDIA_ROOT` — verified by test: after a
  re-submit both files are on disk, and a beginner skip clears the field while
  leaving the file behind.
- **What we decided:** Nothing deletes anything yet. Retention of a student's
  recitation samples is a data-deletion question, which CLAUDE.md says to ask
  about rather than settle silently, so it is logged in `tech-debt.md` (three
  linked entries: local disk, no upload validation, orphaned files) and raised
  with the product owner instead of being fixed in passing.
- **Why it matters for later phases:** "Update the row, don't duplicate it" is a
  pattern that will recur (re-uploads, re-assessments). Any model that swaps a
  `FileField` value needs an explicit answer to *what happens to the old file* —
  the ORM will not volunteer one. Also don't assume moving to object storage
  fixes it; it relocates the orphans.


- **What happened:** A test asserting `ValidationError` on a duplicate
  `Track.slug` failed with `IntegrityError`.
- **What we decided:** Nothing to fix — it's correct. `Level` and
  `PlacementResult` call `full_clean()` in `save()` (cross-table role rules, and
  the gap rule), so *their* uniqueness violations surface as `ValidationError`.
  `Track` has no cross-table rules, so it doesn't, and its unique slug fails at
  the database as `IntegrityError`.
- **Why it matters for later phases:** Same trap as the Phase 1 note above, now
  with a twist: the exception type varies *per model* in the same app. Check
  whether a model calls `full_clean()` in `save()` before writing
  `assertRaises` — and wrap `IntegrityError` expectations in
  `transaction.atomic()` inside a `TestCase`, or the broken transaction fails
  the rest of the test.
