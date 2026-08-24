# Tech debt

Shortcuts taken deliberately for MVP speed. Each entry: what was skipped,
why, and what the real fix looks like when it's time.

Format:

## [date] — short title
- **What was skipped:**
- **Why:**
- **Real fix:**
- **Revisit when:** (e.g. "before first paying sub-teacher onboards")

---

## 2026-08-22 — SQLite instead of Postgres
- **What was skipped:** Real database. `config/settings.py` uses SQLite.
- **Why:** Phase 1 is the identity layer; nothing needs Postgres-specific
  behaviour yet, and SQLite keeps a fresh clone running with zero setup.
- **Real fix:** Postgres via `DATABASE_URL`. Watch for behaviour SQLite is lax
  about — case-sensitive uniqueness on `username`/`email`, and constraint
  enforcement timing.
- **Revisit when:** Before any real user data exists, i.e. before the first
  student signs up.

## 2026-08-22 — `is_minor` never recomputed after signup
- **What was skipped:** Any job or hook that flips `is_minor` to False when a
  student turns 18.
- **Why:** The spec defines `is_minor` as computed at signup, and Phase 1 has
  no behaviour that depends on the transition.
- **Real fix:** Either make `is_minor` a property derived from
  `date_of_birth` (source of truth, always correct), or a nightly job that
  recomputes it. The property is cleaner; it needs the field dropped and the
  register response reworked.
- **Revisit when:** Before anything legal or payment-related keys off minor
  status — likely the parental-consent piece of the payments phase.

## 2026-08-22 — Single-lead-teacher rule is not enforced
- **What was skipped:** Any constraint stopping a second
  `TeacherProfile.is_lead=True` from existing.
- **Why:** The spec explicitly said not to hard-enforce it in the DB yet.
- **Real fix:** A partial unique constraint (`UniqueConstraint(fields=["is_lead"],
  condition=Q(is_lead=True))`) once multi-academy is definitively ruled in or out.
- **Revisit when:** A second lead teacher is actually on the cards.
- **Mitigation now:** No API creates `TeacherProfile`s at all — creation and
  sub-teacher approval are admin-only, so a second lead can't appear through the
  API surface.

## 2026-08-22 — `full_clean()` inside `save()`
- **What was skipped:** A more surgical validation strategy for `ParentLink`
  and `TeacherProfile`.
- **Why:** The role rules span two tables, and validating in `save()` is the
  only way to make them hold for direct ORM writes as well as the API/admin.
- **Real fix:** Move the checks into serializers + admin forms and drop them
  from `save()`, or gate on a `validate=False` kwarg.
- **Revisit when:** Something needs `save(update_fields=...)`, `bulk_create`,
  or `loaddata` on these models — all three are broken or degraded by this.

## 2026-08-22 — Registration has no email verification and issues no token
- **What was skipped:** Confirming the email address, and logging the user
  straight in after signup.
- **Why:** Out of scope for the identity layer; `/api/auth/login/` exists and
  no email backend is configured yet.
- **Real fix:** dj-rest-auth + allauth registration with mandatory email
  verification, and return a token from register so the client skips a step.
- **Revisit when:** Before real signups — an unverified email means a parent
  link request can't be trusted to reach a real guardian.

## 2026-08-22 — Password reset endpoints not wired
- **What was skipped:** `dj_rest_auth.urls` is not included wholesale; only
  login and logout are routed explicitly.
- **Why:** The password-reset views need an email backend and templates that
  don't exist yet, and the spec only asked for login.
- **Real fix:** Configure an email backend, then include the reset/change URLs.
- **Revisit when:** First user forgets their password.

## 2026-08-23 — Placement audio is stored on local disk
- **What was skipped:** Any real file storage. `MEDIA_ROOT` is a directory in
  the project tree, and in `DEBUG` Django itself serves it (`config/urls.py`).
- **Why:** Phase 2 only needs the file to exist so the lead can play it back;
  object storage is configuration, not design, and would slow the phase down.
- **Real fix:** S3 (or equivalent) via `django-storages`, with private objects
  and signed URLs — a recitation sample is a minor's voice, so it must not be
  publicly readable, which is exactly what serving it off `MEDIA_URL` does.
- **Revisit when:** Before the first real student uploads a sample. Of the three
  audio entries here this is the only blocker; the other two are a hardening
  step and an accepted tradeoff.

## 2026-08-23 — Uploaded placement audio is not validated
- **What was skipped:** Any check on the uploaded file's type, size or
  duration. `PlacementResult.audio_sample` is a bare `FileField` and
  `PlacementSubmitSerializer.audio_sample` a bare `FileField`, so a student can
  POST a 2 GB `.exe` and it is accepted as a "recitation sample".
- **Why:** The spec says only "file field, nullable", and the audio/skip
  invariant (the rule the phase actually turns on) cares whether a file is
  present, not what is in it.
- **Real fix:** Validate content type and extension against an audio allowlist,
  cap the size (`DATA_UPLOAD_MAX_MEMORY_SIZE` plus a serializer check), and
  reject on mismatch. Sniff the magic bytes rather than trusting the
  client-supplied `Content-Type`.
- **Revisit when:** The upload endpoint is reachable by anyone who isn't us —
  it is unauthenticated-adjacent (any student account can hit it), so this is
  the cheapest real abuse vector in the codebase so far.

## 2026-08-23 — A placement keeps no record of superseded samples
- **What was skipped:** Any history of recitation samples. A placement holds
  exactly one; replacing or deleting it removes the file (`curriculum/signals.py`).
- **Why:** The retention question was put to the product owner rather than
  guessed, and the call was keep-current-only — cheapest storage, and a deletion
  request is satisfied by deleting the row. Accepted tradeoff: nothing records
  what a *past* review actually listened to, so a levelling decision cannot be
  re-heard after the student re-submits.
- **Real fix:** A `PlacementSample` table — one row per submission, with
  `PlacementResult` pointing at the current one — if the audit trail is wanted.
  That reopens the retention question rather than closing it, so it needs the
  same explicit decision, not a silent addition.
- **Revisit when:** A levelling decision is actually disputed, or sub-teacher
  quality review (Phase 4's purpose) needs to see the sample a level was set
  from. Not before — the current rule is deliberate, not an oversight.

## 2026-08-23 — Teacher specialties are recorded but never enforced
- **Resolved 2026-08-24 (Phase 4).** `Booking.clean()` now rejects a level whose
  track is not in the teacher's `TeacherProfile.specialties`
  (`models.specialty_error`), for routing and direct booking alike, and
  `Cohort.clean()` applies the same rule to a group class's teacher. Creation-only,
  so editing a teacher's specialties cannot freeze bookings they already hold.
  Kept here for the reasoning, not as outstanding work — but see the entries it
  created, below.
- **What was skipped:** Any check that a booking's `level.track` is one of the
  teacher's `TeacherProfile.specialties`. The field exists (added in Phase 3
  with explicit approval, since it changes an already-shipped Phase 1 model) and
  the admin can populate it, but nothing reads it.
- **Why:** The spec says so in as many words: this phase trusts whoever is
  booking to pick a sensible teacher, and Phase 4's matching logic is where
  eligibility actually gets enforced. Building the check now would mean
  building it twice, and the second version has to consider capacity and
  routing reasons the first one can't see.
- **Real fix:** Phase 4 routing reads `specialties` when selecting a teacher. If
  direct booking survives alongside routing, `Booking.clean()` also needs a
  rule rejecting a level whose track the teacher does not teach.
- **Revisit when:** Done, both halves — routing reads it, and direct booking is
  gated by it (`test_routing_api.py::SpecialtyEnforcementAPITests`, acceptance
  criterion 7).

## 2026-08-24 — A teacher with no specialties recorded is unbookable
- **What was skipped:** Any migration or default granting existing teachers a
  track. Phase 4 enforces `specialties`, and the strict reading — no specialties
  means teaches nothing — is what shipped.
- **Why:** It is the correct default for a quality gate: assuming a teacher can
  teach a track nobody recorded them against is exactly the mistake the rule
  exists to prevent, and backfilling "all tracks" would silently undo the gate for
  everyone who already exists. There is one teacher in the system today, so the
  cost is a single admin action.
- **Real fix:** Nothing in code — it is an **onboarding step**: a teacher's tracks
  must be set in the admin before they can be booked or run a cohort. Worth
  surfacing as an admin warning on an approved profile with zero specialties, and
  worth a check in whatever sub-teacher onboarding flow eventually exists.
- **Revisit when:** The first sub-teacher is onboarded by someone who isn't us.
  The failure mode is a confusing "does not teach" 400 rather than anything unsafe,
  but it is confusing at exactly the wrong moment.

## 2026-08-24 — Cancelling a cohort seat leaves the membership behind
- **What was skipped:** Any link between cancelling a seat `Booking` and removing
  the student from `Cohort.students`. `cancel()` sets the status; the M2M row stays.
- **Why:** Cancelling one seat and leaving a group class are arguably different
  acts — a student who misses a session has not dropped the course — and the spec
  says nothing about it. The wrong guess is worse than the gap: auto-removal would
  silently free a seat somebody may still consider theirs.
- **Consequence now:** The student holds a seat with nothing to attend, and the
  class reads as fuller than it is, so `Cohort.open_near` may skip a cohort that
  effectively has room. There is a test asserting the current behaviour
  (`test_routing.py::test_a_routed_seat_keeps_the_student_in_the_cohort_after_cancelling`),
  so a later phase has to change it on purpose.
- **Real fix:** Decide what a cancelled seat means, then either drop the membership
  in `cancel()` (needs the seat-vs-course distinction) or count *scheduled seat
  bookings* rather than M2M rows when deciding whether a cohort is open, plus an
  explicit `leave_cohort()`. The second is more truthful and more work.
- **Revisit when:** A real cohort runs and somebody cancels — a data-quality
  problem before it is a capacity one.

## 2026-08-24 — A cohort is a single session, not a recurring class
- **What was skipped:** Any recurrence on `Cohort`. It has one
  `schedule_start_utc`, per the Phase 4 spec's field list.
- **Why:** That is the spec, and one session is enough to prove routing's
  cohort-first step. Recurrence is a scheduling model in its own right (how many
  weeks, what happens to a cancelled week, how a student joins late), and inventing
  one here would have been a large silent decision.
- **Real fix:** A `CohortSession` table — one row per occurrence, seats hanging off
  the session rather than the cohort — or a recurrence rule plus generated sessions.
  This also makes the weekly capacity count more exact: cohort deduplication in
  `weekly_committed_minutes` is keyed on `cohort_id`, which only equals "per
  session" while a cohort *has* one session.
- **Revisit when:** The first real group class is offered. A weekly beginner class
  is the actual product (mvp-spec section 2), and today a lead would have to create
  one cohort per week.

## 2026-08-24 — Sub-teacher selection ignores timezone overlap
- **What was skipped:** The mvp-spec's step 3 matches subs by "specialty, timezone
  overlap, and remaining capacity". Only specialty and capacity are read.
- **Why:** Availability already carries the answer implicitly — a teacher whose
  declared hours cover the requested UTC instant is by construction awake for it. A
  separate timezone score would either duplicate that or start weighting
  candidates, and the Phase 4 spec explicitly forbids growing step 3 into a scoring
  system.
- **Real fix:** Nothing, unless "overlap" is meant to mean something availability
  cannot express — preferring a teacher for whom the slot is mid-morning over one
  for whom it is the last hour of their day, say. That is a ranking decision.
- **Revisit when:** The rubric-ranking entry below, so both are decided together.

## 2026-08-24 — Sub-teacher ranking is remaining capacity only
- **What was skipped:** Any quality signal in routing's step 3. The sub with the
  most remaining weekly minutes wins; ties break on lowest pk for determinism.
- **Why:** The Phase 4 spec asks for exactly this and names the temptation to
  resist: "simplest correct rule for this phase: whoever has the most remaining
  weekly capacity (spreads load evenly). Don't build anything fancier
  (rubric-average-based ranking, etc.) yet — log it in tech-debt.md if you're
  tempted." This is that log entry.
- **Real fix:** Once the assessment rubric exists, rank on some combination of
  rubric average and capacity. That is a real design decision — what weight, and
  what happens to a new sub-teacher with no scores — not something to back into.
- **Revisit when:** The assessment phase has produced enough rubric data for an
  average to mean anything.

## 2026-08-23 — No teacher-facing API for editing availability
- **What was skipped:** Any write endpoint for `Availability`. The API is
  read-only (`GET /api/scheduling/availability/?teacher_id=`); hours are
  maintained in the Django admin.
- **Why:** The spec's API surface lists exactly one availability endpoint and it
  is the public read. It is also not just CRUD boilerplate: one local window can
  convert into *two* UTC rows, so a naive ModelForm/serializer would silently
  drop half of any window that crosses midnight UTC.
  `Availability.create_from_local()` exists and returns a list for that reason.
- **Real fix:** A teacher-scoped viewset that takes local weekday/start/end plus
  the teacher's zone, calls `create_from_local`, and treats the returned rows as
  one logical window — which means a grouping key on the model so the two halves
  can be edited and deleted together.
- **Revisit when:** A sub-teacher who isn't us needs to set their own hours.
  Until then availability entry is a staff action.

## 2026-08-23 — Nothing stops a booking in the past
- **Resolved 2026-08-24 (Phase 3.5).** `Booking.clean()` now rejects a new
  scheduled booking starting before `now - PAST_BOOKING_GRACE` (30s), gated on
  `_state.adding` so a session that has merely been taught stays saveable.
  Kept here for the reasoning, not as outstanding work.
- **What was skipped:** Any "start_time_utc must be in the future" rule.
- **Why:** The spec doesn't ask for one, and the rules it does ask for
  (declared hours, no overlap) are indifferent to when "now" is. Availability is
  a weekly rule, so last Monday 09:00 is as inside the window as next Monday's.
- **Real fix:** A `clean()` rule rejecting a new booking whose start is in the
  past, with a small grace period so a booking made at 09:00:01 for 09:00 isn't
  refused. It has to be creation-only, like the availability check, or existing
  bookings become unsaveable the moment they end.
- **Revisit when:** Done. The consequence that made it more than cosmetic — a
  completed session's slot reading as free, since the overlap rule only
  considers *scheduled* bookings — is now closed off by the past-start rule
  rather than by changing the overlap rule.

## 2026-08-23 — Overlap detection compares ranges in Python
- **What was skipped:** Doing the overlap check in SQL, or as a database
  constraint.
- **Why:** `duration_minutes` is an integer, not an interval, so
  `start + duration` is backend-specific — and the project is still on SQLite
  (see the entry above), which has neither range types nor exclusion
  constraints. `Booking.clashing_bookings()` therefore filters to a bounded
  candidate set in SQL and compares ends in Python.
- **Real fix:** On Postgres, a `tstzrange` generated column plus an
  `ExclusionConstraint` on `(teacher, range)` where `status='scheduled'` — which
  makes the rule race-proof *in the database*, rather than by agreement between
  writers. That also retires `TeacherBookingLock` and the `transaction_mode`
  setting the mitigation below depends on. **Phase 4 caveat:** the constraint must
  carry the same exemption `clashing_bookings()` now has — seats sharing a
  `cohort_id` do not clash, because a group class is one teacher teaching once. A
  constraint without it breaks every cohort the moment it is applied.
- **Revisit when:** The Postgres move. No longer urgent — the race it describes
  is mitigated (below), so this is now about deleting a workaround, not about
  correctness. The candidate query is bounded by `LONGEST_POSSIBLE_BOOKING`, so
  this was never a performance issue.
- **Mitigation now (Phase 3.5):** Creating a booking holds a per-teacher
  `TeacherBookingLock` row inside `transaction.atomic()`, so the overlap check
  and the INSERT are one atomic unit and a second request for the same slot is
  refused by `clean()` instead of committing. This leans on
  `DATABASES["default"]["OPTIONS"]["transaction_mode"] = "IMMEDIATE"`; without
  it SQLite takes its write lock too late and the pair deadlocks into
  "database is locked" rather than queueing. Both halves have tests
  (`scheduling/tests/test_concurrency.py`) that fail if either is removed.
  Two things it does *not* do: it only guards creation, since no supported
  operation moves an existing booking onto a new slot (reschedule is
  cancel-and-rebook), and it serialises through a row rather than the database,
  so it is only as good as every writer remembering to take it — which is the
  precise weakness the exclusion constraint above removes.


## 2026-08-22 — `SECRET_KEY` and `DEBUG` are hardcoded
- **What was skipped:** Forcing these to be set from the environment.
- **Why:** Keeps a fresh clone runnable with no setup.
- **Real fix:** Raise on a missing `DJANGO_SECRET_KEY` when `DEBUG` is False,
  and add the standard production security settings (HSTS, secure cookies,
  `SECURE_SSL_REDIRECT`).
- **Revisit when:** First deploy to anything reachable from the internet.
