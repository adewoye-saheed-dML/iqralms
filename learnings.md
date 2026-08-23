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

## 2026-08-23 — Re-submitting a placement orphaned the old audio file
- **What happened:** The spec's "a second placement request updates the existing
  row" rule is implemented by reassigning `audio_sample` in
  `PlacementResult.submit()`. Django has not deleted the displaced file since
  1.3, so the old sample stayed in `MEDIA_ROOT` — proven by test: after a
  re-submit both files were on disk, and a beginner skip cleared the field while
  leaving the file behind.
- **What we decided:** Asked rather than guessed, because retention of a
  student's recording is a data-deletion call. Product owner chose **keep only
  the current sample**: `curriculum/signals.py` deletes the displaced file on
  replace and the current file on delete. Two implementation points that are
  easy to get wrong:
  - Deletion is deferred to `transaction.on_commit`. File storage is not
    transactional, so deleting inline would destroy the file even if the write
    that displaced it rolled back, leaving a row pointing at nothing. `TestCase`
    never commits, so those tests need
    `self.captureOnCommitCallbacks(execute=True)` or they silently assert
    nothing.
  - Registering the receivers is what takes `PlacementResult` off Django's
    fast-delete path, and that is the only reason cleanup fires for *cascades* —
    deleting a `User` cascades to their placements. Remove the receivers and
    cascade cleanup silently stops.
- **Why it matters for later phases:** "Update the row, don't duplicate it" will
  recur (re-uploads, re-assessments). Any model that swaps a `FileField` value
  needs an explicit answer to *what happens to the old file* — the ORM will not
  volunteer one. The accepted tradeoff (no record of what a past review heard)
  is in `tech-debt.md`; moving to object storage does not change any of this.


## 2026-08-23 — `Track.slug` uniqueness raises `IntegrityError`, not `ValidationError`
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

## 2026-08-23 — A weekday+time availability rule cannot be DST-correct
- **What happened:** `Availability` stores a weekday plus two UTC *times*, per
  the spec. A bare time carries no date, so converting a teacher's local window
  to UTC needs a reference date — and the answer differs by an hour either side
  of a DST boundary. The same "Mondays 18:00 New York" is 22:00 UTC in July and
  23:00 UTC in January. One stored row cannot be both.
- **What we decided:** Convert against the *next* occurrence of the weekday
  (`utils.next_date_for_weekday`), which makes every window correct as of now,
  and take the drift on the chin. `local_window_to_utc` accepts an `on_or_after`
  argument so tests can pin the reference date instead of being seasonal. Not
  papered over: it is the best a weekday+time model can do.
- **Why it matters for later phases:** Twice a year, a DST-observing teacher's
  stored hours are an hour wrong until someone re-enters them. The real fix is
  storing the window in the teacher's zone plus their zone name and converting at
  query time — a schema change to `Availability`, so it needs a decision, not a
  patch. Timezones without DST (Africa/Lagos, Asia/Karachi — much of the
  expected roster) are unaffected, which is why this is not a launch blocker.

## 2026-08-23 — Converting one local window can produce two UTC rows
- **What happened:** A Lagos teacher's "Monday 00:30–02:30" is Sunday
  23:30–midnight *plus* Monday 00:00–01:30 in UTC. One local window, two UTC
  days, and a different weekday than the teacher named.
- **What we decided:** `local_window_to_utc` returns a *list* of segments and
  `Availability.create_from_local` creates one row per segment. A window may
  never wrap past midnight UTC — `clean()` requires `end > start` — so the split
  is the only representation. Related: an end at exactly 00:00 UTC is stored as
  `time.max` (`utils.END_OF_DAY`), because 00:00 would violate `end > start`.
  That sentinel does *not* round-trip: 20:00 local reads back as
  19:59:59.999999. Harmless, and there is a test saying so — the same sentinel
  is applied to a booking's segments, so a session ending at UTC midnight still
  matches its window.
- **Why it matters for later phases:** Anything that edits or deletes "a window"
  must handle the two-row case, which is exactly why there is no teacher-facing
  hours editor yet (`tech-debt.md`). A UI showing stored rows to a teacher will
  also show them a Sunday window they think they set on Monday — show
  `local_window()`, never the raw UTC fields.

## 2026-08-23 — Booking validation is split on purpose: creation-only vs always
- **What happened:** Running every rule on every `save()` made an existing
  booking unsaveable as soon as a teacher narrowed their hours — and therefore
  *uncancellable*, since `cancel()` goes through `save()`. A student could be
  stuck with a session neither side could get rid of.
- **What we decided:** The availability check runs only when
  `self._state.adding`; the overlap check runs for as long as the booking is
  `scheduled` (so a cancelled booking stops blocking its slot, which is what
  makes cancel-and-rebook the supported reschedule path). Both have tests
  pinning the distinction.
- **Why it matters for later phases:** The teacher-approval gate is deliberately
  *not* creation-only, so it has the frozen-booking problem the availability
  check was fixed for: revoking a teacher's approval makes their live bookings
  unsaveable, cancellation included. There is a test documenting this. The
  operational order is cancel first, then unapprove — and if Phase 4 ever
  revokes approval automatically (quality control is its purpose), it must cancel
  that teacher's scheduled bookings in the same transaction or it will wedge
  them.

## 2026-08-23 — A student can double-book themselves across two teachers
- **What happened:** The overlap rule is per teacher. Nothing stops one student
  from holding two bookings at the same instant with two different teachers.
- **What we decided:** Left as-is — the spec explicitly says this isn't Phase 3's
  problem to solve, and asks for it to be flagged here rather than silently
  constrained. There is a test asserting the permissive behaviour, so a later
  phase that decides otherwise has to change a test on purpose instead of
  discovering the rule by accident.
- **Why it matters for later phases:** The check is a mirror of
  `clashing_bookings()` with `student` swapped for `teacher`. Worth adding when
  bookings are paid for (a student paying for two overlapping sessions is a
  refund conversation), and it belongs in `clean()` next to the teacher rule, not
  in a serializer.
