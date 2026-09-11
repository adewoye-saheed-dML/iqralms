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

## 2026-08-24 — `select_for_update()` does nothing on SQLite, silently
- **What happened:** Phase 3.5's spec offered "`select_for_update()` on the
  teacher's `Availability`, or a dedicated per-teacher lock row" as equivalent
  ways to close the booking race. They are not equivalent here. Django's SQLite
  backend sets `has_select_for_update = False`, and `QuerySet.select_for_update()`
  on a backend without it is a **no-op that raises nothing** — the query runs
  without `FOR UPDATE` and locks nothing. It would have read like a fix in review
  and held no lock at runtime.
- **What we decided:** A `TeacherBookingLock` row per teacher, acquired by
  *bumping* it (`revision = F("revision") + 1`) inside `transaction.atomic()`
  wrapping `full_clean()` + the INSERT. A write is the one thing that takes a
  real lock on every backend: a row lock where the engine has them, SQLite's
  database write lock where it doesn't. `acquire()` raises if it finds itself
  outside an atomic block, because a lock released at the end of its own
  statement protects nothing — the exact failure the class exists to prevent.
- **Why it matters for later phases:** Never reach for `select_for_update()`
  while this project is on SQLite; check `connection.features` before trusting
  any locking primitive. When Phase 4 routing writes bookings, it must go through
  `Booking.save()` so it takes the lock — a `bulk_create` of assignments would
  bypass both the lock and `clean()` entirely.

## 2026-08-24 — SQLite's default transaction mode turns the race into a crash
- **What happened:** The lock alone wasn't enough. SQLite defaults to
  `BEGIN DEFERRED`, which takes no lock until the first *write*. Two bookings for
  one slot would both BEGIN, both read (`clashing_bookings`), then both try to
  upgrade to a write lock — and neither can wait, because each needs something
  the other holds. SQLite breaks the tie by returning "database is locked"
  immediately, *ignoring the busy timeout entirely*. Measured directly: the
  read-then-write pattern under DEFERRED gives one committed row and one
  `OperationalError`; under IMMEDIATE both transactions queue and both complete.
- **What we decided:** `DATABASES["default"]["OPTIONS"]["transaction_mode"] =
  "IMMEDIATE"` (Django 5.1+), so the write lock is taken at BEGIN and the second
  booking queues instead of deadlocking, plus an explicit `timeout` for how long
  it may queue. The loser then re-reads, sees the committed booking, and is
  refused by the overlap rule — a 400, not a 500.
- **Why it matters for later phases:** This setting is load-bearing, not
  cosmetic, and `test_concurrency.py` fails without it. It is also *the* reason
  the lock works on SQLite at all: `BEGIN IMMEDIATE` serialises writes
  database-wide, so on this stack the lock row is really about being correct on
  Postgres later, where locking is per row and BEGIN takes nothing. Retire both
  together when the exclusion constraint lands.

## 2026-08-24 — The concurrency tests forced the test database onto disk
- **What happened:** The threaded tests failed with `database table is locked`
  even with everything above in place. Django's SQLite test database is
  in-memory, opened as `file:memorydb_default?mode=memory&cache=shared` — and
  **shared-cache mode locks per table and raises `SQLITE_LOCKED`, which the busy
  timeout is never consulted for.** So the loser died instantly instead of
  queueing. That is an artefact of shared-cache mode; production is file-backed
  and blocks properly. Confirmed by running the same two-thread probe against
  both: shared-cache in-memory errors, a real file serialises.
- **What we decided:** `DATABASES["default"]["TEST"]["NAME"]` points at a real
  file, so the suite exercises the locking rules being shipped. That cost ~200ms
  per commit in fsync (8s suite → minutes), so `config/test_runner.py` sets
  `PRAGMA synchronous=OFF` on test connections via `connection_created`. Suite is
  ~11s. Deliberately **not** `journal_mode=WAL`: WAL is faster still but changes
  the locking rules (readers stop blocking writers), and the locking rules are
  the thing under test. `synchronous` touches durability only.
- **Why it matters for later phases:** Any future test that needs two real
  connections (threads, `TransactionTestCase`, `LiveServerTestCase`) depends on
  the test database being a file — don't "optimise" it back to `:memory:`. And a
  concurrency test that passes should be distrusted until it has been seen to
  fail: each half of this mechanism was verified by removing it and watching the
  suite go red, which is the only reason we know the tests test anything.

## 2026-08-24 — A cohort of six is six Booking rows the overlap rule must not reject
- **What happened:** Phase 4's spec adds `Booking.cohort`, "set when a booking is
  a cohort seat". Six students in one group class is therefore six `Booking` rows
  with the same teacher at the same instant — which Phase 3's per-teacher overlap
  rule rejects. The second student in every cohort would have been unbookable.
- **What we decided:** Asked rather than guessed, because it relaxes a rule from
  an already-committed phase. Product owner chose **seats stay bookings**:
  `clashing_bookings()` excludes rows sharing this booking's `cohort_id`. The
  relaxation is deliberately narrow — a 1:1 session still clashes with a seat, and
  two *different* cohorts at one time still clash, because those really are one
  teacher in two places. Tests pin all three cases. The payoff is that a seat is an
  ordinary booking: it gets a video room, it can be cancelled, and payouts will see
  it. The alternative (membership only, no rows) would have left `Booking.cohort`
  permanently null and `routed_reason=cohort_assigned` with nowhere to live.
- **Why it matters for later phases:** `cohort_id` is now load-bearing for the
  overlap rule, so anything that writes a `Booking` with a cohort must be sure the
  cohort is the one that session belongs to — `Booking.clean()` enforces that the
  teacher, level and start all match, precisely so a mismatched row cannot opt
  itself out of the overlap check. The Postgres exclusion constraint in
  tech-debt.md must carry the same exemption, or every cohort breaks on migration.

## 2026-08-24 — Counting only `scheduled` minutes would have leaked the weekly cap
- **What happened:** Phase 4's spec defines the capacity check as "total
  `scheduled` booking minutes for the current week". Taken literally, a teacher's
  load *drops* as Monday's sessions are marked `completed`, so a 20-hour teacher
  could be booked to 20 hours again by Thursday. The cap would have been per-open-
  booking, not per week.
- **What we decided:** Asked; product owner chose **everything except cancelled**
  (`CAPACITY_CONSUMING_STATUSES` = scheduled + completed + no_show). Cancelling is
  the one thing that gives capacity back. A `no_show` counts because the teacher
  held the slot and turned up. Also: a cohort session counts **once**, not once per
  seat — charging per seat would make cohorts look more expensive than the 1:1
  sessions they exist to replace, and a six-seat class would eat three hours of a
  cap for thirty minutes of teaching.
- **Why it matters for later phases:** Payouts must read
  `weekly_committed_minutes` / `week_bounds` rather than assembling their own sum,
  which is exactly what the spec asked for ("don't let the routing check and any
  future payout calculation use different week boundaries"). Note the cohort
  deduplication is by `cohort_id`, which is exact only while a cohort has a single
  `schedule_start_utc` — a recurring cohort needs it per session, not per cohort.

## 2026-08-24 — Enforcing specialties broke 20 existing tests, all legitimately
- **What happened:** Closing the Phase 3 tech-debt item (`specialties` recorded but
  never read) made `Booking.clean()` reject a level whose track the teacher does
  not teach. Twenty Phase 1-3 tests failed instantly — every one that built a
  booking outside `BookingFactory` and expected success, because a fresh teacher
  and a fresh level share no track.
- **What we decided:** A teacher with *no* specialties recorded teaches nothing.
  That is the strict reading and the right default for a quality gate, but it means
  an existing teacher is unbookable until their tracks are set (logged in
  tech-debt.md as an onboarding step, not a bug). `BookingFactory._create` and
  `CohortFactory._create` grant the specialty before saving, so a factory still
  cannot build a state the model would reject — it has to be `_create` rather than
  a `post_generation` hook, because those run *after* `save()`, which is where the
  rule fires. Tests that want the rejection construct their `Booking` directly and
  simply don't call the new `teaches()` helper.
- **Why it matters for later phases:** "Bookable" is no longer a property of a
  teacher alone; it is a property of a teacher *and* a level. Any future fixture
  or test that pairs the two needs `teaches()` first. The rule is creation-only,
  like the availability and cap rules, so editing a teacher's specialties cannot
  freeze the bookings they already hold — the same trap documented above for the
  approval gate, avoided this time on purpose.

## 2026-08-24 — An M2M cap cannot live in `clean()`
- **What happened:** Phase 4 asks for `Cohort.max_students` to be enforced "in
  `clean()` or a custom `add_student()` method, not just at the serializer layer".
  `clean()` cannot do it: an M2M is written *after* the row is saved, so
  `full_clean()` on a new cohort runs before any student is attached and has
  nothing to count — and `cohort.students.add(...)` never calls `save()` or
  `clean()` at all. It is a write straight to the join table.
- **What we decided:** Both. `Cohort.add_student()` is the supported path and
  raises `CohortFull`; a `pre_add` `m2m_changed` receiver
  (`scheduling/signals.py`) is the backstop that catches a raw `.add()`, including
  the reverse direction (`user.cohort_memberships.add(cohort)`). One trap worth
  knowing: Django runs `add()` inside `atomic(savepoint=False)`, so raising from
  the receiver marks the surrounding transaction as needing rollback. Under
  `TestCase` — itself a transaction — every query after the refusal then dies with
  `TransactionManagementError`, so those tests wrap the refusal in
  `transaction.atomic()` for the savepoint. In production (autocommit) `add()`'s
  own block is outermost and the refusal leaves the connection perfectly usable;
  verified directly rather than assumed.
- **Why it matters for later phases:** Same shape as the `IntegrityError` note
  above — the exception a rule raises depends on *where* the rule lives. Any future
  M2M with a cap (a waitlist, a cohort roster edit) needs the receiver, not
  `clean()`, and its tests need the savepoint.

## 2026-08-24 — Routing tests a candidate by building an unsaved Booking
- **What happened:** Routing needs to know whether a teacher *could* take a
  session — declared hours, no clash, approved, right specialty, under cap, not in
  the past. Reimplementing those checks in `routing.py` would have meant two
  copies of every eligibility rule, guaranteed to drift.
- **What we decided:** `routing._candidate()` builds an unsaved `Booking` and calls
  `full_clean()`. A `ValidationError` means "not eligible, and here is why";
  clean means "eligible, and this is the row to save". So routing knows *no* rules
  of its own, and a rule added to `Booking.clean()` later is picked up for free.
  The refusals are kept as `{field: [{code, message}]}` and returned in
  `NoCapacity.considered` / `Routed.considered`, which is what makes the 409 body
  explainable to a parent instead of a bare error — and what lets the tests assert
  on error *codes* rather than on prose.
- **Why it matters for later phases:** The candidate is validated, then saved —
  and `save()` re-runs everything under the teacher's lock, so a candidate that
  loses its slot to a concurrent write is refused rather than quietly reassigned.
  Do not "optimise" the second validation away; it is the whole reason routing is
  race-safe. The preferred-teacher waitlist (a later phase) should extend
  `considered` rather than inventing a second reporting shape.

## 2026-08-24 — The booking factory quietly produced past-dated sessions
- **What happened:** `BookingFactory` derives its start from
  `slot_at(window)`, which used `next_date_for_weekday(weekday)` — and that
  returns **today** when today already is the window's weekday. The default
  window is Monday 09:00-17:00, so on a Monday afternoon every factory-built
  booking was several hours in the past. Harmless for Phases 1-3, which have no
  opinion about "now"; the moment the past-start rule landed it would have failed
  a large part of the suite, and only on Mondays after 09:00 UTC.
- **What we decided:** `slot_at` defaults its reference date to *tomorrow*, so
  every derived slot is future-dated whatever the wall clock says, while a caller
  that genuinely wants a past slot passes `on_or_after` explicitly (the API test
  for the past rule does). Fixed in the factory rather than in
  `utils.next_date_for_weekday`, whose "today counts" behaviour is correct for
  the production availability conversion that uses it.
- **Why it matters for later phases:** Test data that depends on the clock is a
  latent, calendar-dependent failure. Anything time-sensitive added later should
  derive from a factory helper rather than composing `datetime`s inline, and the
  near-midnight case still can't be built at all — a 30-minute session in the
  last half hour of the UTC day crosses midnight, which no single availability
  window can cover, so those tests skip themselves rather than assert the wrong
  error code.

## 2026-08-25 — Paying more buys no queue position (the spec's open question)
- **What happened:** `specs/phase-5-pricing-waitlist.md` refused to decide whether
  a `PricingAgreement` with `reason=premium_direct` should raise a student's
  `TeacherWaitlist.priority`, and said to ask rather than default either way.
- **What we decided:** **No.** Nothing in the code writes `priority` at all — a
  lead raises it by hand in the admin or it stays 0. The two models are
  neighbours in one phase, not coupled: `pricing` imports nothing from
  `scheduling` and vice versa, and `test_waitlist.py::PricingDoesNotTouchPriorityTests`
  asserts that creating a premium agreement leaves an existing entry's priority
  untouched *and* that an entry created afterwards still starts at zero.
- **Why it matters for later phases:** If this is ever reversed, the coupling has
  to be deliberate and one-directional — priority computed when the *waitlist
  entry* is created, reading `PricingAgreement.active_for()`, never a pricing
  write reaching across to mutate a queue. And it needs an answer for the entries
  that already exist at that moment, which is the part that makes it a migration
  rather than a feature flag.

## 2026-08-25 — The waitlist/hard-block line is drawn on error *codes*
- **What happened:** The spec wants a preferred teacher who is *full or busy* to
  produce a waitlist entry, but a hard block (unapproved profile, wrong track) to
  "fail outright, not waitlist" — because a queue cannot fix "she does not teach
  Hifz". Routing has no rules of its own (`_candidate()` asks `Booking.clean()`),
  so it has nothing to branch on except the refusal it got back.
- **What we decided:** `routing.WAITLISTABLE_CODES` is an explicit allowlist of
  three `ValidationError` codes — `outside_availability`,
  `teacher_weekly_capacity_exceeded`, `teacher_double_booked` — and
  `is_waitlistable()` requires **every** code in a refusal to be in it. A teacher
  who is both unapproved *and* full is refused outright, because a waitlist entry
  would promise a slot that only approving them can reach.
- **Why it matters for later phases:** This is the one place where routing
  *interprets* `Booking.clean()` rather than merely relaying it, so the two can
  drift after all. **Any new refusal code added to `Booking.clean()` silently
  defaults to "fail outright, no waitlist"** — which is the safe direction, but it
  is a decision being made by omission. Add the code to the frozenset, or
  deliberately don't, and say which in the commit. The same set governs the
  post-lock race path: a candidate that passes the check and then loses the slot
  inside `save()` is re-classified through `is_waitlistable()` and waitlisted, so
  a lost race and a slot that was already gone produce the same answer for the
  family.

## 2026-08-25 — A waitlist entry validates the teacher's role but not their approval
- **What happened:** `TeacherWaitlist.clean()` looked like it should reuse
  `bookable_teacher_error()`, the way `Booking`, `Availability` and `Cohort` all
  do. Doing that would have walked straight into the frozen-row trap this file
  already records for the booking approval gate: if a named teacher's approval is
  later revoked, every existing entry naming them becomes unsaveable — so
  stamping a fulfilment, or even adjusting `priority`, would raise.
- **What we decided:** Only `is_teacher` (the role) is checked. An entry is a
  *request*, not a session: the fuller test belongs at the moment a booking is
  actually created, which is `promote_waitlist_entry()` → `_candidate()` →
  `Booking.clean()`, and that is where an unapproved teacher is refused. Two
  tests pin it — an unapproved teacher can still be asked for, and an entry stays
  saveable after its teacher loses approval.
- **Why it matters for later phases:** "Validate everything everywhere" is the
  wrong instinct for any row that outlives the state it describes. The question to
  ask of a new model is not "which rules apply" but "which rules must still hold
  in a year, when the world has moved on" — and for a record of something somebody
  *wanted*, that is almost always fewer rules than the thing they wanted.

## 2026-08-25 — Superseding has to happen before `full_clean()`, not after
- **What happened:** `PricingAgreement` enforces one active agreement per student
  and level with a partial `UniqueConstraint`, and `save()` calls `full_clean()`
  like every other model in this project. The obvious ordering — validate, save,
  then deactivate the row this one replaces — cannot work: `full_clean()` checks
  constraints, so creating the replacement is refused by the very rule it is
  about to satisfy.
- **What we decided:** `save()` calls `supersede_active()` **first**, inside the
  same `transaction.atomic()`, then `full_clean()`, then the INSERT. So the old
  row is already `active=False` by the time the constraint is evaluated. The
  deactivation is a queryset `update()` rather than a `save()` per row, on purpose:
  it flips one boolean on otherwise-untouched historical records, and routing them
  through `full_clean()` would re-validate rows nobody is editing.
- **Why it matters for later phases:** Any "new row supersedes the old one" model
  with a partial unique constraint has this ordering hazard, and it fails *only*
  on the second write — so a test that creates one agreement proves nothing. There
  is a test that hand-writes a second active row and asserts the database refuses
  it, which is what proves the constraint is real rather than decorative.

## 2026-08-25 — Two `mine/` endpoints, two different answers about parents
- **What happened:** Phase 5 adds `/api/scheduling/waitlist/mine/` and
  `/api/pricing/agreements/mine/`. Both started `IsStudent`-only, copying
  `/bookings/mine/`. But `/route/` is `IsStudentOrParent`, so a parent can put
  their child on a teacher's waitlist and then could not read the request back —
  a hole rather than a privacy boundary.
- **What we decided:** Asked rather than guessed, since it is about who in a
  family sees what. The waitlist listing was widened to `IsStudentOrParent`,
  scoped through `ParentLink` exactly as `BookingCancelView` scopes cancellation
  (and `.distinct()`, because a student with two linked parents joins twice).
  Pricing stays student-only: there is no parent write path to pricing, so nothing
  is broken by leaving it, and a parent reading a negotiated rate is the payments
  phase's decision to make. The asymmetry is deliberate and both halves are in
  tech-debt.md.
- **Why it matters for later phases:** The rule worth carrying forward is
  narrower than "parents can see their children's things": **whoever can create a
  record through the API must be able to read it back.** Check that pairing when
  adding any `mine/` endpoint — the permission class on the *write* path is the
  one that tells you who the audience is.


## 2026-08-26 — The Postgres move was almost entirely uneventful, and the one surprise was in the test suite
- **What happened:** tech-debt.md had warned since Phase 1 to watch for
  "behaviour SQLite is lax about — case-sensitive uniqueness on `username`/`email`,
  and constraint enforcement timing". Neither materialised. All 649 pre-existing
  tests passed on PostgreSQL, and the only two that failed were asserting on the
  media URL the storage change removed — nothing to do with the database. The
  reason the warnings did not bite: this codebase validates in `save()` via
  `full_clean()`, so uniqueness and cross-table rules are enforced by Django
  *before* the write, and the database's own enforcement timing was never what the
  tests were exercising.
- **What we decided:** Nothing needed changing to preserve the invariants — which
  is the answer the phase wanted, since the goal was to preserve them, not to
  rediscover them. What *did* change is runtime: the suite went from ~8s to ~220s,
  and almost all of it is `TransactionTestCase`. Those tests truncate every table
  after each test instead of rolling a transaction back, which is cheap on a local
  SQLite file and not on PostgreSQL. The 14 concurrency tests alone account for
  ~130s of it.
- **Why it matters for later phases:** Don't reach for `TransactionTestCase`
  casually now — it costs roughly ten seconds a test rather than nothing. It is
  still the correct and only tool when threads have to see each other's committed
  rows, which is every test in `test_concurrency.py`. If the suite becomes painful
  the lever is `--parallel`, not converting those tests back to `TestCase`, which
  would silently stop testing the race.

## 2026-08-26 — `select_for_update()` was a no-op on SQLite, so the booking lock had to be a write
- **What happened:** Phase 3.5 took the per-teacher lock by *bumping a counter*
  rather than by `select_for_update()`, which reads like a workaround until you
  know why: Django's SQLite backend sets `has_select_for_update = False`, and
  `select_for_update()` on a backend without the feature does nothing at all. It
  would have read as a fix and held no lock. Phase 6 could finally make it say
  what it means.
- **What we decided:** Keep `TeacherBookingLock` — but prove it, because the phase
  spec said the table could stay only if tests showed it was still needed.
  `LockIsStillRequiredOnPostgresTests` patches `acquire()` out, forces both writers
  past the overlap check before either inserts, and gets the double booking;
  a control test with nothing patched gets one booking and one clean refusal. Two
  details that made those tests work: `list()` around the `select_for_update()`
  queryset, because a lazy queryset locks nothing, and forcing the interleaving
  with a barrier rather than racing for it, because a test whose job is to prove a
  race exists must not depend on winning one.
- **Why it matters for later phases:** PostgreSQL's default READ COMMITTED does
  not make a check-then-insert safe. Any *new* rule of the form "read the
  neighbours, then write" — a second booking writer, a cohort seat allocator, a
  payout run — needs the lock taken the same way, or the database constraint that
  retires it (tech-debt.md). And the general lesson: a concurrency primitive that
  silently degrades to nothing on one backend is worse than one that raises. That is
  why `acquire()` still refuses to run outside `transaction.atomic()`.

## 2026-08-26 — Validating an upload at the model layer must not read the stored file
- **What happened:** The obvious way to add a model-level backstop for upload
  validation is a field validator or a `clean()` rule that inspects
  `self.audio_sample`. Both are traps once storage is a bucket: `FieldFile.file`
  *opens the stored object* when the value came from the database, so a rule like
  that turns every save of every placement — a review stamp, an admin edit — into
  an S3 GET, and would also fail an old file if the rules were later tightened.
- **What we decided:** `PlacementResult.clean()` validates only when
  `audio_sample._file` is an `UploadedFile`. That is precisely "bytes that arrived
  over HTTP in this process", which is precisely CLAUDE.md's untrusted input — a
  plain `File` from a factory, fixture or data migration is trusted code writing a
  known file. Reading the private `_file` attribute rather than the public `.file`
  property is deliberate and the reason is written at the call site: the public one
  triggers the storage round-trip the check exists to avoid. Serializer validation
  stays the primary path; this only catches the admin's upload widget and any
  future view that assigns `request.FILES[...]` straight to the model.
- **Why it matters for later phases:** Any model rule that touches a `FileField`
  needs to ask "does this open the file, and on whose save?" before it ships.
  Assessment recordings and payout exports are the next candidates.

## 2026-08-26 — A signed URL is a bearer capability, so authorisation happens before minting
- **What happened:** Making placement audio private raised a question the public
  `MEDIA_URL` never did: the download endpoint for local development cannot require
  authentication, because an `<audio src>` element sends no Authorization header.
  That looks like a hole until you notice it is exactly how a presigned S3 URL
  behaves.
- **What we decided:** Put the authorisation in the *minting* endpoint
  (`/audio-url/`, which is where the lead-or-owning-student rule lives) and treat
  the token as a capability, like the presigned URL it stands in for. The download
  view re-checks three things instead of re-authenticating: the signature and its
  age, that the token names *this* placement (so editing the pk in the path does
  nothing), and that the placement still holds the object the token was minted for.
  That last check gives Phase 2's keep-only-the-current-sample rule a useful
  property for free: re-submitting invalidates every outstanding URL immediately,
  with no revocation list. Every failure is the same 404, so probing a token tells
  you nothing about which part was wrong.
- **Why it matters for later phases:** The pattern generalises to anything private
  a browser has to fetch directly — assessment recordings, payout statements,
  exported reports. Authorise at mint time, keep the TTL short, put the object's
  identity inside the signature, and never widen who can mint without asking. The
  five-minute TTL is also why a `SECRET_KEY` rotation is currently low-risk
  (tech-debt.md): it breaks at most five minutes of in-flight playback.

## 2026-08-30 — A rubric PATCH could be refused with half of it already written
- **What happened:** Writing the Phase 7 API tests turned up a gap the model
  tests could not see. `AssessmentRubricUpdateSerializer.update()` saved the
  rubric first and then applied each criterion, so a single PATCH that renamed
  the sheet *and* touched a criterion illegally answered **400** with the rename
  already committed. Both reachable failure modes did it, and they fail in
  different layers: a criterion id belonging to another rubric is refused by the
  serializer, and a criterion moved onto an occupied `order` is refused by the
  model's `full_clean()`. `AssessmentRubricCreateSerializer.create()` had the
  worse version of the same shape — a criterion failing mid-loop would have left
  the track's previous rubric *superseded* by a half-built replacement, because
  `AssessmentRubric.save()` deactivates the old row before the criteria are
  written.
- **What we decided:** One `transaction.atomic()` around each of the two write
  paths, so a 400 leaves the configuration exactly as the lead left it. The DRF
  `ValidationError` raised inside the block propagates out of it, which is what
  rolls the savepoint back — the conversion of the model's `ValidationError`
  stays outside, where it was. Two API tests pin it (`RubricEditAPITests`), and
  they were confirmed to fail without the fix rather than merely to pass with it.
- **Why it matters for later phases:** Django validates per `save()`, so
  "several rows, validated individually" is only atomic if something makes it
  atomic. Any serializer that writes a parent row and then children — a cohort
  and its seats, a payout run and its lines — needs the same wrapper, and a model
  whose `save()` retires a previous row (this one, `PricingAgreement`) makes the
  partial write actively destructive rather than merely untidy.

## 2026-08-30 — Phase 8's four financial conventions were decided, not inferred
- **What happened:** The Phase 8 spec's "stop and ask" list turned out to be
  load-bearing. Four things it names were genuinely undecided in the repository,
  and each had a plausible wrong answer: the repository had **no currency and no
  monetary rounding convention** anywhere (`pricing` stores bare decimals);
  `TeacherProfile.hourly_payout_rate` is **null for the lead**, who nonetheless
  teaches bookings; and a cohort session is stored as **one `Booking` row per
  seat**, so the spec's "one payout per booking" shape would have paid a teacher
  six times for teaching one group class once.
- **What we decided (product owner, 2026-08-30):**
  * The rate source is the existing `hourly_payout_rate`. No second rate model.
  * Currency is `NGN`, snapshotted onto every record; amounts quantize to `0.01`
    with `ROUND_HALF_UP`, **once**, at the end of `payouts.models.payout_amount`.
    `assessment` already rounds `ROUND_HALF_UP`, so this follows it.
  * A cohort session is paid **once**, not once per seat — the same reasoning
    `scheduling.weekly_committed_minutes` already applies to capacity. The payout
    attaches to the earliest seat and denormalizes `cohort` so the database can
    hold "one payout per cohort session" as a partial unique constraint.
  * A teacher with **no rate is not payout-eligible**. The lead's own sessions
    produce no record and generation reports them as `no_payout_rate` rather than
    failing the run or writing an amount of zero.
- **Why it matters for later phases:** These are now conventions, not choices. A
  payments phase that introduces a second currency has to decide what the
  existing `NGN` rows mean; a performance-pay phase has to say explicitly that it
  is overriding `Family pricing ≠ Teacher payout`; and a recurring-cohort feature
  has to revisit the `cohort_id` deduplication in both this app and capacity
  accounting (tech-debt.md).

## 2026-08-30 — A statement is computed, so it has no id to fetch
- **What happened:** The spec suggests `GET /api/payouts/statements/mine/{id}/`
  while also saying a statement is "a reporting view over payout records, not a
  replacement for those records" and "do not duplicate financial facts". Those
  two pull in opposite directions: an addressable statement implies a stored row,
  and a stored total is a second copy of money that can drift from the records it
  summarises.
- **What we decided:** No `TeacherStatement` model. `services.statement_for`
  aggregates the payout rows for `[period_start, period_end)` on every request, so
  the total is arithmetically the sum of the rows returned beside it. The period
  *is* the identifier: `GET /api/payouts/statements/mine/?start=&end=`, with both
  bounds required. `StatementStatus` distinguishes `empty` (nothing generated for
  this period) from a real zero, the same way assessment distinguishes a missing
  assessment from a score of zero.
- **Why it matters for later phases:** If a payment run ever needs to reference
  "the statement we paid against", that is when a stored statement earns its
  place — and it should store the payout ids it covered rather than a recomputed
  total, so the two can still be checked against each other.

## 2026-08-30 — Three "status" choice sets need three names
- **What happened:** Adding `PayoutStatus` and `StatementStatus` made
  `drf-spectacular` emit three enum-collision warnings, because `Booking.status`
  had been the only choice set called `status` in the schema. This project's
  definition of done includes `check --deploy --fail-level WARNING`, and the
  schema had zero warnings before the phase, so a resolvable-but-unnamed enum is
  a regression rather than a cosmetic nit.
- **What we decided:** Name all three in `SPECTACULAR_SETTINGS.ENUM_NAME_OVERRIDES`
  (`BookingStatusEnum`, `PayoutStatusEnum`, `StatementStatusEnum`), the same way
  `RoleEnum` was handled in Phase 1. Also worth knowing: a `SlugRelatedField` on a
  plain `Serializer` (a computed dataclass, no `Meta.model`) warns for a different
  reason — there is no model for it to resolve against, so a `CharField` over
  `source="teacher.username"` is the correct shape there.
- **Why it matters for later phases:** Any phase adding a `status`, `reason` or
  `role` field to a serializer should expect to add a name for it, and should
  regenerate the schema (`manage.py spectacular --validate`) before calling the
  phase done. The check is cheap and the warning is invisible until someone looks.

## 2026-09-05 — Organization is the tenant; membership is the relationship
- **What happened:** SaaS Phase 1 had to introduce multi-tenancy into a backend
  built for one academy, where `User.role` (`lead`/`sub`/`student`/`parent`)
  already drives booking, pricing, assessment and payout behaviour. The tempting
  shortcuts were both wrong: reusing `User.role` as the organization role, or
  putting an `organization` foreign key on `User`. The first conflates "what is
  this person to the teaching business" with "what authority do they have inside
  this academy"; the second hard-codes one user, one academy.
- **What we decided:** `Organization` is the tenant, and `OrganizationMembership`
  is the only link between a user and one. A user may hold memberships in several
  academies with a different `OrganizationRole` (`owner`/`admin`/`staff`/`teacher`)
  in each, and `organization + user` is unique so there is exactly one row per
  pair — no membership history, no invitation states, and reactivation flips
  `status` rather than adding a row. `User.role` is neither read nor written by
  the `organizations` app: a student who founds an academy is its owner and is
  still a student. Ownership is likewise *only* a membership whose role is
  `owner` — there is deliberately no `Organization.owner` field, because two
  representations of ownership is one too many.
- **Why it matters for later phases:** SaaS Phase 3 (accounts tenancy) is where
  the two role systems finally have to meet, and it inherits a clean question
  rather than a merged field: does `OrganizationMembership.role == teacher`
  eventually subsume `User.role in (lead, sub)`, or do they stay orthogonal? Any
  phase that adds tenant scoping to an existing domain reads membership to answer
  "who may", never `User.role`.

## 2026-09-05 — Tenant access is an *active* membership, and that decided three shapes
- **What happened:** The phase spec states the boundary as
  `organization access == active OrganizationMembership`, which sounds like one
  rule but forces three separate API decisions: what `GET /organizations/{id}/`
  says to a non-member, what `/mine/` shows a suspended member, and what
  `Organization.is_active` does.
- **What we decided:** One function, `organizations.models.active_membership()`,
  answers the boundary for every endpoint, and it returns `None` for an outsider,
  a suspended member and an anonymous caller alike — the endpoint cannot
  distinguish them, so it cannot leak which organization ids exist (an unknown id
  and someone else's academy both answer 403). `/mine/` lists active memberships
  only: a suspended member reading their academy's name and timezone through it
  would be the one hole in the rule. Inside a tenant the 404/403 split follows the
  repository's existing precedent — a membership id belonging to *another*
  organization is a 404 from the scoped queryset, because a 403 would confirm the
  row exists. `Organization.is_active` is stored and read by nothing: disabling an
  academy is a workflow with consequences for the people inside it, and inventing
  it here would have been a product decision the spec assigns to a later phase.
- **Why it matters for later phases:** Every domain that becomes tenant-scoped
  should route its "may this caller reach this tenant" through
  `active_membership()` rather than re-deriving it, so widening access is one edit
  in one place. And the academy-settings phase owns the decision of what
  `is_active=False` actually does — until then, nothing behaves as though it means
  anything.

## 2026-09-05 — One owner, enforced three times, and no way to transfer it
- **What happened:** "Exactly one initial owner, created by organization creation,
  and no public API creates a second" is the invariant the whole ownership model
  rests on, and there are three ways to break it: a request body asking for
  `role: owner`, a PATCH promoting an existing member, and a write that bypasses
  the API altogether.
- **What we decided:** Three layers, one per attack. The serializers offer
  `ASSIGNABLE_ORGANIZATION_ROLES` (`admin`/`staff`/`teacher`), so no membership
  request can name `owner` at all. `permissions.OwnerMembershipIsProtected`
  refuses every write to the owner's existing row — to an admin *and* to the
  owner, because suspending or demoting it is an ownership transfer rather than a
  role edit. And a partial `UniqueConstraint` on `organization` where
  `role = owner` is the database's backstop, which also holds for the Django admin
  and a hand-written INSERT. Creation itself is one `transaction.atomic()` block:
  an organization whose owner membership is refused does not remain committed.
- **Why it matters for later phases:** Ownership transfer is now a deliberate
  piece of work rather than something that can happen by accident — it has to
  demote and promote inside one transaction and reckon with that constraint
  explicitly, which is the intent. The phase spec's endpoint list also omits any
  way to change a membership while its rules require owner and admin to suspend,
  reactivate and re-role members, so `PATCH /api/organizations/{id}/memberships/{id}/`
  was added to make those rules performable; `PUT` deliberately is not, because
  `organization` and `user` are not editable fields.

## 2026-09-05 — `TeacherProfile` has eleven readers, so Phase 2 built beside it
- **What happened:** The obvious way to make teacher configuration tenant-aware is to
  move `approved`, `max_weekly_hours` and `hourly_payout_rate` off the global
  `OneToOneField(User)` profile. The audit found eleven production consumers of that
  row first: `bookable_teacher_error` and `specialty_error`, the weekly cap in
  `Booking.clean()` and in `route_session`, `lead_teacher()` and
  `matching_sub_teachers()` (both of which filter on it *in SQL*),
  `payouts.services.applicable_rate`, `/me/`'s serializer, the admin, and the
  `select_related` in two of them. Phase 2 is explicitly forbidden from rewriting
  scheduling or payout behaviour, so moving the fields would have meant rewriting
  both in the same change that introduced the model.
- **What we decided:** Leave `TeacherProfile` byte-identical and add
  `accounts.OrganizationTeacherConfiguration` beside it, hanging off
  `OrganizationMembership` rather than off a `(user, organization)` pair — the
  membership already *is* "this user in this academy", with the uniqueness rule and
  the status field to match. The new table is written by its own API and read by
  nothing else: each later tenancy phase flips one consumer at a time and can
  backfill from the global profile. The field classification was explicit rather than
  assumed: `approved`, `max_weekly_hours` and `hourly_payout_rate` are per-academy;
  `bio` describes the person and stayed global; `specialties` stayed untouched because
  it points at the still-global `curriculum.Track`; and `is_lead` was deliberately
  *not* copied, because academy leadership is already `OrganizationMembership.role`
  and a third representation after `User.role` and `TeacherProfile.is_lead` would be
  one more pair to keep in step. Migration `0003` is one `CREATE TABLE` — no `ALTER`,
  no backfill, nothing deleted, zero existing rows touched.
- **Why it matters for later phases:** Scheduling tenancy and payout tenancy each
  inherit a storage boundary that already exists and is already tested, so neither
  has to invent one while also rewriting booking or payroll. The cost is a stated
  interim overlap: three fields live in two places and only the old one is
  authoritative (see tech-debt.md).

## 2026-09-05 — Membership is belonging; the organization role is only authority
- **What happened:** Phase 2's rules are all of the form "is this person an active
  *parent* / *student* / *teacher* of this academy", and there are two fields that
  could answer: `User.role` and `OrganizationMembership.role`. Reading the wrong one
  produces two opposite bugs — treat the organization role as a teaching identity and
  a parent account becomes a teacher; require it and a lead teacher who founded their
  own academy stops being able to teach in it, because their row says `owner`.
- **What we decided:** Belonging is `status == active`, full stop, and it is always
  read through `organizations.active_membership()`. The *account* role supplies the
  rest of the answer. So `accounts.tenancy`'s helpers are the conjunction of the two
  and never look at the organization role at all, which is why a founder's `owner`
  membership can carry teaching terms and a `teacher` membership on a parent account
  cannot. Nothing in the accounts app re-derives what "active" means; a second status
  check that drifted out of step with the first is the whole failure this arrangement
  avoids.
- **Why it matters for later phases:** Every domain that becomes tenant-scoped should
  key belonging off `active_membership()` and keep asking `User.role` its own
  questions, rather than reaching for the organization role because it is nearer. It
  also leaves the owner/lead migration free to happen later without Phase 2 having
  pre-committed to an answer.

## 2026-09-05 — A `ParentLink` is a family fact, so the tenant question is a different endpoint
- **What happened:** The phase spec offers an organization-scoped parent-link route
  and simultaneously forbids two competing parent-link APIs, so the decision had to be
  made rather than deferred. `POST /api/accounts/parent-links/` had no clients beyond
  its tests — the frontend does not exist yet — so backwards compatibility was a
  choice, not a constraint.
- **What we decided:** Creation stays global and account-level. A parent registers,
  then links to their child with the child's signup code, before any academy is
  involved; making creation organization-scoped would have required an academy to
  admit both parties *first*, which inverts the real onboarding order and would have
  been a new product rule Phase 2 was told not to invent. `/my-children/` also stays
  global, which the spec permits because it returns only global account fields — no
  academy owns them. What became organization-scoped is the *read* that academies
  actually need: `GET /api/accounts/organizations/{id}/children/`, which answers with
  the linked children who are active members *there*. So one global relationship gives
  a parent in two academies two different answers, and Academy A can never surface a
  student it has not admitted.
- **Why it matters for later phases:** The rule lives in exactly one place,
  `tenancy.children_in_organization()`, so scheduling and assessment tenancy can adopt
  it instead of each re-deriving "may this parent act for this child here". Their
  current `ParentLink` authorization checks are still global and are recorded as debt.

## 2026-09-05 — Organization-scoped routes live in the domain, not in `organizations/urls.py`
- **What happened:** The spec's suggested address for the new endpoints was
  `/api/organizations/{id}/parent-links/`, which would have put an accounts-domain
  view into the organization app's URL module — and would have had curriculum,
  scheduling, pricing, assessment and payout tenancy all editing that one file in
  turn.
- **What we decided:** `/api/<domain>/organizations/<organization_id>/<resource>/`.
  The tenant is still in the URL, which is what matters — one mixin
  (`organizations.views.OrganizationScopedMixin`) resolves it to the caller's verified
  membership, and the organization app's own permission classes decide access, so
  there is no second membership or permission implementation. Each domain keeps its
  own routes.
- **Why it matters for later phases:** Phases 3 to 7 each add their organization-scoped
  surface under their own prefix without touching another app, and every one of them
  inherits the same URL-to-membership-to-queryset shape. A cross-tenant id is a 404
  from the scoped queryset rather than a 403, following the precedent Phase 1 set.

## 2026-09-05 — The "who can teach" rule sits where teaching is configured, not on the membership
- **What happened:** The spec asks the system to refuse an impossible teaching
  identity — a `parent` account holding `OrganizationMembership.role = teacher` — but
  also says the policy must preserve existing behaviour, and Phase 1 deliberately kept
  the organization app from reading `User.role` at all. Enforcing it on the membership
  would have broken `OrganizationMembershipFactory`'s default and a set of shipped
  Phase 1 tests, which is a strong signal it is a behaviour change to a finished
  phase rather than a Phase 2 fix.
- **What we decided:** `OrganizationMembership` keeps its meaning untouched — a
  `teacher` row is authority inside the academy, the least-privileged role, and it
  claims nothing about teaching. The account rule is enforced one step further in,
  where teaching is actually configured: `OrganizationTeacherConfiguration.clean()`
  refuses any membership whose user is not `lead` or `sub`, which is the same
  `TEACHER_ROLES` rule `TeacherProfile` has always applied. The API repeats it so the
  400 lands on the field the client sent, and the model backstops the admin and direct
  ORM writes.
- **Why it matters for later phases:** "This person teaches at this academy" is now a
  row that exists, not a role value to be interpreted, which is what scheduling
  tenancy will need to filter on. The residual looseness — a `teacher` membership on
  an account that cannot teach is accepted and means authority only — is recorded as
  debt rather than left implicit.

## 2026-09-05 — `Track.organization` is the only tenant column, and everything else derives
- **What happened:** The obvious way to make curriculum academy-aware is to put an
  `organization` foreign key on every model in it — `Track`, `Level`,
  `PlacementResult` — so every queryset can filter on one column. The spec says not
  to for `Level` "unless a concrete integrity/performance requirement proves it
  necessary", and it turned out to be the right instruction for `PlacementResult`
  too.
- **What we decided:** One stored column, `Track.organization`. `Level.organization`
  and `PlacementResult.organization` are read-only properties that go through the
  track, and every queryset joins (`track__organization`) rather than reading a local
  copy. `TeacherTrack` derives its academy from `membership.organization` and
  validates that it equals `track.organization`.
- **Why it matters for later phases:** A second copy of the owning academy is a second
  thing that can disagree with the first, and "a curriculum object has exactly one
  unambiguous owning academy" is the invariant the whole tenant boundary rests on. A
  `Level` whose stored organization differed from its track's would be a row with two
  owners and no way to say which is right. Scheduling and pricing point at `Level`,
  so when they become tenant-scoped they should derive from `level.track.organization`
  rather than add their own column.

## 2026-09-05 — Slug uniqueness moving to `(organization, slug)` changed the exception type
- **What happened:** Phase 2 recorded that a duplicate `Track.slug` raises
  `IntegrityError` rather than `ValidationError`, because `Track` had no cross-table
  rules and therefore did not call `full_clean()` in `save()`. SaaS Phase 3 gave it
  one — ownership is immutable after creation, which has to be checked against the
  stored row — so it now validates like `Level` and `PlacementResult` do.
- **What we decided:** `Track.save()` calls `full_clean()`, and a duplicate slug within
  one academy is a `ValidationError` on `__all__` from the `UniqueConstraint`. The old
  global unique index is gone rather than kept: leaving it would have made the first
  academy to claim `tajweed` the owner of that word platform-wide.
- **Why it matters for later phases:** The per-model exception-type trap Phase 2
  documented is now resolved in one direction — every model in `curriculum` validates
  inside `save()`. Check before writing `assertRaises`; the answer has changed once
  already.

## 2026-09-05 — The placement membership rule had to go in the model, and it moved the factories
- **What happened:** "The student is an active member of the academy that owns this
  track" is a rule spanning three tables, and the spec asks for it in model/service
  code rather than only in serializers. Adding it to `PlacementResult.clean()` broke
  ten existing tests immediately — every one of which built a placement without a
  membership, because before this phase there was nothing to be a member of.
- **What we decided:** Keep the rule in `clean()`, and make the factories provision
  what the model now requires. `PlacementResultFactory._create()` admits the student —
  and the reviewer, when there is one — into the track's academy *before* the row is
  saved, because a `post_generation` hook runs after `save()` and would be too late.
  The `admit()` helper is idempotent, so a test that wants a suspended membership
  builds it first and keeps it.
- **Why it matters for later phases:** Every tenancy phase after this one will hit the
  same wall: an invariant that spans the membership table invalidates fixtures written
  when memberships did not exist. Putting the fix in `_create()` rather than relaxing
  the invariant is what keeps the rule true for the admin, for data migrations and for
  direct ORM writes — and the ten failures were all legitimate, which is the signal
  that the rule was worth having.

## 2026-09-05 — The pre-SaaS curriculum had no determinable academy, so the migration refuses
- **What happened:** The development database held two tracks, two levels, two
  placements and one recorded teacher specialty — and zero organizations. The spec's
  "stop and ask" list names exactly this case, so it was put to the product owner
  rather than guessed at.
- **What we decided (product owner, 2026-09-05):** name the academy explicitly. The
  data migration resolves the owner or refuses: no unowned tracks is a no-op, exactly
  one organization is unambiguous, `SAAS_LEGACY_CURRICULUM_ORGANIZATION=<pk|slug>`
  settles anything else, and every remaining case raises with instructions. No academy
  is ever created by the migration — an academy is a business with an owner, and a
  migration cannot decide who that is.
- **What the backfill does create:** memberships, for the users who *already hold*
  curriculum data in the named academy — the students with placements, the leads who
  reviewed them, the teachers with recorded specialties. It has to: a placement is
  only readable in an academy when its student is an active member there, so leaving
  legacy students outside would make their existing placements invisible. Nobody else
  is admitted, and no existing membership is touched — a suspended member stays
  suspended, because a migration silently restoring revoked access is worse than a
  migration that does too little.
- **Why it matters for later phases:** the resolution logic lives in
  `curriculum/legacy.py` rather than inside the migration, so it is unit-tested;
  and `curriculum/tests/test_legacy_migration.py` runs the real migrations against a
  database rolled back to the nullable state. Scheduling, pricing, assessment and
  payout tenancy each face the same question, and the same shape — resolve, or refuse
  with instructions — should be reused rather than reinvented.

## 2026-09-05 — Retiring the public track list was cheaper than narrowing it
- **What happened:** `GET /api/curriculum/tracks/` was public and returned
  `Track.objects.all()`. Once tracks are academy-owned that endpoint lists every
  tenant's curriculum, which §19 of the spec forbids outright.
- **What we decided (product owner, 2026-09-05):** retire it. There is no frontend
  yet and no external client — only curriculum's own tests referenced it — so the
  coverage moved to `/api/curriculum/organizations/{id}/tracks/` and the route is
  gone. The alternative, keeping it authenticated and narrowed to "every academy you
  belong to", would have added a cross-academy read shape the spec does not ask for.
- **Why it matters for later phases:** the same judgement applies to the global
  placement routes, which went with it. Two ways into the same data means one of them
  is eventually forgotten, and the forgotten one is the one that stops being checked.
  The only global route left in the app is the token-gated audio download, and it is
  global because the token — not the path — is what authorises it.

## 2026-09-05 — Teacher curriculum eligibility hangs off the membership, not the profile
- **What happened:** `TeacherProfile.specialties` is a global many-to-many to `Track`.
  With tracks academy-owned, that relation lets one academy's roster decide what a
  teacher may teach in another — and it cannot express the thing the phase exists for,
  a teacher who teaches Tajweed at Academy A and Arabic at Academy B.
- **What we decided:** a new `curriculum.TeacherTrack`, keyed on
  `OrganizationMembership` and `Track`, validated so the two agree about the academy.
  The membership already means "this user in this academy", already carries the
  uniqueness rule for that pair, and already knows whether the relationship is live —
  the same reasoning `accounts.OrganizationTeacherConfiguration` follows. The legacy
  relation is untouched: `Booking.clean()`, `Cohort.clean()` and `routing` still read
  it, and Phase 3 is forbidden from rewriting either.
- **Why it matters for later phases:** SaaS Phase 4 is what switches those readers
  over, and it now has somewhere to read *from*. Until it does, the two relations
  overlap and only the old one is enforced — a stated interim state, recorded in
  tech-debt.md rather than left to be discovered.

## 2026-09-05 — A serializer that narrows a queryset per tenant has to fail closed
- **What happened:** The write serializers narrow their `track` and
  `recommended_level` querysets to `self.context["organization"]` in `get_fields()`,
  which is the security boundary — a foreign id is *absent* rather than forbidden, so
  it comes back as "object does not exist" without confirming the row exists
  elsewhere. Reading the context with `[...]` made schema generation fail: drf-spectacular
  instantiates serializers bare, with no context at all.
- **What we decided:** declare every scoped field with `Model.objects.none()` and
  narrow it only when the context supplies an organization. Outside a request the
  empty queryset is left alone, which is right for a schema — and if a view ever
  forgot to put the organization in its context, the endpoint would reject every id
  rather than accept any.
- **Why it matters for later phases:** the failure mode of a tenant-scoped field must
  be "nothing matches", never "everything matches". `queryset=Model.objects.all()` as
  a declared default would have looked identical in every test and been a
  platform-wide lookup the first time a view was written without the context.

## 2026-09-11 — Availability has an explicit tenant column because it has no curriculum relation
- **What happened:** In SaaS Phase 3, curriculum established that `Track.organization` is the single stored curriculum tenant boundary and `Level` derives its organization through its track. For scheduling, `Booking`, `Cohort`, and `TeacherWaitlist` all point directly or indirectly to `Level`, so they derive their organization through `level.track.organization`. However, `Availability` specifies a recurring weekly UTC window for a teacher and has no relationship with `Track` or `Level`.
- **What we decided:** `Availability` alone gets an explicit `organization = models.ForeignKey(Organization, on_delete=models.CASCADE)`. The local-to-UTC conversion (`Availability.create_from_local`) takes the organization, and windows that wrap past midnight UTC are created with that organization. To prevent foreign user leakage, `Availability.clean()` enforces that the teacher has an active membership in `self.organization`.
- **Why it matters for later phases:** Having a stored `organization` only on `Availability` keeps the schema normalized without redundant organization columns on `Booking`, `Cohort`, or `TeacherWaitlist`. If an availability editing API is introduced later, it filters and validates on `self.organization`.

## 2026-09-11 — Human physical time is global while workload capacity is tenant-scoped
- **What happened:** In a multi-tenant platform, a teacher can belong to multiple academies (e.g. Academy A and Academy B). Each academy manages its own terms: weekly cap (`max_weekly_hours`), approved status, and track permissions. However, a human teacher cannot physically teach in two places at once. If Academy A books a teacher for Monday 10:00–11:00 UTC, that teacher cannot simultaneously teach a student from Academy B at Monday 10:30–11:30 UTC.
- **What we decided:** Overlap detection (`clashing_bookings()`) and concurrency control (`TeacherBookingLock`) remain **globally scoped to the teacher**, ignoring organization boundaries. In contrast, weekly capacity counters (`OrganizationTeacherConfiguration.max_weekly_hours` / `weekly_committed_minutes`) are **tenant-scoped**, counting only bookings within the same organization. A cancelled booking yields capacity back in that organization. Cohort deduplication remains intact: a cohort session counts once toward the teacher's weekly capacity in that organization, regardless of how many seats are booked.
- **Why it matters for later phases:** Never attempt to scope the session overlap check or `TeacherBookingLock` by organization. Cross-tenant double booking would immediately occur if `clashing_bookings()` filtered by `level__track__organization`.

## 2026-09-11 — `TeacherBookingLock` is permanently global per teacher
- **What happened:** Phase 3.5 and Phase 6 introduced `TeacherBookingLock` to serialize booking writes and prevent race conditions (double bookings). During tenancy design, the question arose whether the lock should be per-(teacher, organization) or per-teacher.
- **What we decided:** Global per teacher (`models.OneToOneField(User, ...)`). When a booking is being validated and inserted for teacher $T$ under Academy A, acquiring the lock serializes against any concurrent booking for teacher $T$ under Academy B as well. Acquisition uses `SELECT ... FOR UPDATE` on PostgreSQL inside `transaction.atomic()`.
- **Why it matters for later phases:** The physical human cannot double-book across tenants. Locking at the tenant level would allow two concurrent booking attempts in different academies to interleave, pass `clashing_bookings()`, and commit overlapping sessions.

## 2026-09-11 — Deprecating `TeacherProfile` authority in favor of `OrganizationTeacherConfiguration` and `TeacherTrack`
- **What happened:** In legacy single-tenant phases, `TeacherProfile.approved`, `TeacherProfile.max_weekly_hours`, and `TeacherProfile.specialties` governed teacher eligibility. In multi-academy SaaS, teacher approval and weekly capacity belong to `accounts.OrganizationTeacherConfiguration`, while track qualifications belong to `curriculum.TeacherTrack`.
- **What we decided:** All scheduling validation — `Booking.clean()`, `Cohort.clean()`, `matching_sub_teachers`, and `route_session()` — migrated away from `TeacherProfile` authority. A teacher is bookable in an academy only if:
  1. The user has an active membership in the academy (`organizations.active_membership()`).
  2. The teacher is approved in that academy (`OrganizationTeacherConfiguration.approved == True`).
  3. The level's track is active in the teacher's `TeacherTrack` for that academy.
  4. The booking does not exceed the teacher's `OrganizationTeacherConfiguration.max_weekly_hours`.
  The legacy fields on `TeacherProfile` are kept untouched for backwards compatibility until remaining consumers (`TeacherProfile.hourly_payout_rate` in Phase 7 payouts, and `TeacherProfile.bio`) are audited.
- **Why it matters for later phases:** A teacher can now teach Tajweed in Academy A with 10 weekly hours, while teaching Arabic in Academy B with 5 weekly hours, with independent approval and capacity tracking.

## 2026-09-11 — Scheduling endpoints are mounted under `/api/scheduling/organizations/<organization_pk>/...`
- **What happened:** Previously, scheduling routes were global (e.g. `/api/scheduling/bookings/`, `/api/scheduling/route/`).
- **What we decided:** All scheduling endpoints were moved under `/api/scheduling/organizations/<organization_pk>/...` using `AcademyScopedView(OrganizationScopedMixin)` and `AcademyScopedSerializerMixin`. Scoped serializers validate `level`, `student`, and `teacher` querysets against the active organization, returning a 400 "does not exist" on foreign IDs (preventing information leakage). Legacy unscoped scheduling routes were retired completely.
- **Why it matters for later phases:** Consistency across the platform: all domain-specific APIs follow the `/api/<domain>/organizations/<organization_pk>/...` pattern established in Phase 2 and Phase 3.

## 2026-09-11 — Derived tenancy via `Level -> Track -> Organization` avoids duplicate columns on `PricingAgreement`
- **What happened:** In SaaS Phase 5, pricing agreements needed to be scoped to the owning academy. `PricingAgreement.level` is non-nullable and protected (`PROTECT`). Every `Level` points to a `Track`, and every `Track` has a non-nullable `Organization` (enforced in SaaS Phase 3).
- **What we decided:** Do not add an `organization` column to `PricingAgreement`. The derived relationship `agreement.level.track.organization` is unambiguous, prevents data drift, and provides an authoritative single source of truth. Query filtering is encapsulated in `PricingAgreementQuerySet.in_organization(org)` and the model exposes `@property def organization(self)`.
- **Why it matters for later phases:** Normalized schemas avoid synchronisation bugs. Whenever an entity's parent hierarchy already has an immutable tenant anchor, deriving tenancy is cleaner than denormalizing.

## 2026-09-11 — `PricingAgreement.clean()` enforces active membership for student and approver
- **What happened:** A pricing agreement establishes rates between a student and an academy, approved by a lead teacher. If a student or approver belongs to Academy B while the level belongs to Academy A, cross-tenant leaks occur.
- **What we decided:** Enforce active memberships at the model layer in `clean()`. Both `student` and `approved_by` must hold active memberships in `self.organization` (`OrganizationMembership.status == ACTIVE`). Suspended members are rejected. `PricingAgreement.save()` calls `full_clean()`, guaranteeing that ORM writes, admin saves, and API endpoints are strictly guarded.
- **Why it matters for later phases:** Cross-tenant protection must not rely exclusively on API serializer querysets. Having database model-level `clean()` validation guarantees data integrity even when rows are created via management commands or background jobs.

## 2026-09-11 — Non-destructive legacy pricing remediation and partial unique constraints
- **What happened:** Legacy pricing agreements created prior to SaaS Phase 5 might have students or approvers without `OrganizationMembership` rows. Furthermore, `PricingAgreement` defines a database-level partial unique constraint (`unique_active_pricing_per_student_level` where `active=True`).
- **What we decided:** Data migration `0002_remediate_legacy_pricing` runs `remediate_legacy_pricing()` which admits unadmitted students as `STAFF` and approvers as `TEACHER` with `ACTIVE` status. Existing suspended memberships are left untouched. Duplicate active agreements (if any) are resolved non-destructively: the newest agreement remains active, while older agreements are deactivated (`active=False`), preserving complete historical audit trails without deleting data.
- **Why it matters for later phases:** Zero data loss during tenancy backfills. Historical negotiations and pricing records remain intact and queryable by leads.

## 2026-09-11 — Assessment tenancy derived through Track and Booking hierarchy without denormalized columns
- **What happened:** Assessment domain models (`AssessmentRubric`, `AssessmentCriterion`, `SessionAssessment`, `AssessmentScore`, `ProgressSnapshot`) all possess deterministic, non-nullable relationships to either `Track` (`AssessmentRubric.track`, `ProgressSnapshot.track`) or `Booking -> Level -> Track` (`SessionAssessment.booking`).
- **What we decided:** We did not add a redundant `organization` foreign key column to any of the 5 models. Instead, we implemented `.in_organization(org)` on Custom QuerySets and `@property def organization(self)` on each model.
- **Why it matters for later phases:** Single source of truth. Denormalized organization columns would risk drifting from `booking.level.track.organization` or `track.organization`.

## 2026-09-11 — Defense-in-depth model invariants on `SessionAssessment.clean()` and `ProgressSnapshot.clean()`
- **What happened:** Cross-tenant assessment leakage can happen if a teacher in Academy A assesses a booking in Academy B, or if an assessment references a student or reviewer not admitted to that academy.
- **What we decided:** `SessionAssessment.clean()` strictly validates that:
  1. The student is an active member of `self.organization`.
  2. The teacher (`assessed_by`) is an active member and configured in `self.organization`.
  3. The booking belongs to the assessment's track's organization.
  4. Any lead reviewer (`lead_reviewed_by`) is an active lead member in `self.organization`.
  `AssessmentScore.clean()` validates that the score's criterion belongs to the same organization as the assessment.
  `ProgressSnapshot.clean()` validates active memberships for the student and generator.
  `save()` calls `full_clean()`.
- **Why it matters for later phases:** Direct ORM writes, background jobs, and management commands cannot bypass tenant boundaries.

## 2026-09-11 — Assessment routes mounted under `/api/assessment/organizations/<organization_pk>/...` and legacy routes retired
- **What happened:** Pre-SaaS assessment routes were flat and unscoped (e.g. `/api/assessment/rubrics/`, `/api/assessment/mine/`).
- **What we decided:** All 16 assessment endpoints were prefixed with `organizations/<organization_pk>/` and protected with `IsAuthenticated, IsOrganizationMember`. Scoped serializers validate related fields against `self.organization`. Foreign resource lookups return 404 (not 403) to prevent existence leakage.
- **Why it matters for later phases:** Unscoped endpoints cannot provide a cross-tenant bypass vector.

