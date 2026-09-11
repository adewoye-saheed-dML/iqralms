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
- **Resolved 2026-08-26 (Phase 6).** PostgreSQL is the canonical database, via a
  required `DATABASE_URL`. There is deliberately no fallback: a missing or
  non-PostgreSQL URL raises `ImproperlyConfigured` at settings import, because a
  fallback is exactly how SQLite became canonical in the first place. The whole
  suite runs against PostgreSQL, `docker-compose.yml` gives a fresh clone a local
  one, and `config/tests/test_settings.py` pins the no-fallback rule. Kept here
  for the reasoning, not as outstanding work.
- **What was skipped:** Real database. `config/settings.py` uses SQLite.
- **Why:** Phase 1 is the identity layer; nothing needs Postgres-specific
  behaviour yet, and SQLite keeps a fresh clone running with zero setup.
- **Real fix:** Postgres via `DATABASE_URL`. Watch for behaviour SQLite is lax
  about — case-sensitive uniqueness on `username`/`email`, and constraint
  enforcement timing.
- **Revisit when:** Before any real user data exists, i.e. before the first
  student signs up.
- **What the move actually cost:** less than the warnings above suggested. All
  649 pre-existing tests passed on PostgreSQL with two one-line changes, and both
  were about the private-storage change rather than the database. The uniqueness
  and constraint-timing worries did not materialise — see learnings.md,
  2026-08-26, for why, and for the one thing that did change (suite runtime).

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
- **Resolved 2026-08-26 (Phase 6).** `PlacementResult.audio_sample` goes to a
  private S3-compatible bucket (`config.storage.PrivateS3Storage`, required in
  production). Objects are never publicly readable: `querystring_auth` makes every
  URL presigned, `default_acl=None` sends no ACL, and the API publishes
  `has_audio_sample` and a bare filename instead of a path. Playing a sample means
  asking `/api/curriculum/placements/{id}/audio-url/` for a URL that expires in
  five minutes. Development and tests use `PrivateLocalStorage`, whose `url()`
  *raises* — there is no `MEDIA_URL` and no media route left to fall back to.
  Kept here for the reasoning, not as outstanding work.
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
- **Who can hear a sample** was put to the product owner rather than inferred
  from the old public URL (2026-08-26): the lead teacher, who reviews it, and the
  student whose voice it is. Sub-teachers and a minor's linked parent are
  refused. The parent case is the one worth knowing about — it is defensible
  product-wise, but granting it is a *new* permission, and a hardening phase must
  not widen who can reach student data. `test_audio_access.py` asserts the 403 so
  changing it later has to be deliberate.

## 2026-08-26 — Development files under `MEDIA_ROOT` were discarded, not migrated
- **What was skipped:** Any migration of existing local `MEDIA_ROOT` files into
  the private bucket during the Phase 6 storage change.
- **Why:** There were none to migrate — no `media/` directory existed in the
  working tree at the time of the change, the path has been gitignored since
  Phase 2, and no deployment of this application has ever existed. Inventing a
  production data migration for files that were never production data would be
  ceremony, and the phase spec says so explicitly.
- **Real fix:** None needed. If a developer has local samples from Phases 2-5,
  they are development artefacts: delete the directory, or re-submit through the
  API, which is now the only path that writes to storage.
- **Revisit when:** Never for this transition. It matters again only if a *future*
  storage change happens after real uploads exist, at which point a copy step
  between buckets is real work with a real cutover.

## 2026-08-23 — Uploaded placement audio is not validated
- **Resolved 2026-08-26 (Phase 6).** `curriculum/validators.py` caps size at
  15 MiB and allowlists six formats (mp3, wav, ogg, m4a, webm, flac), checking the
  extension, the file's own leading bytes, and the declared content type — in that
  order, with the signature being the one that decides. `Content-Type` alone is
  never trusted, so an executable renamed `recitation.mp3` and declared
  `audio/mpeg` is refused. Enforced at the serializer for clean field errors and
  again in `PlacementResult.clean()` for the admin's upload widget, which was the
  one HTTP path that bypassed the serializer. `DATA_UPLOAD_MAX_MEMORY_SIZE` sits
  just above the per-file cap so an oversized body is refused before our code runs.
  Kept here for the reasoning, not as outstanding work.
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
- **Still not checked: duration.** The size cap bounds it loosely and nothing in
  the product reads a sample's length, so it stayed out. A real duration check
  needs to decode the container (ffprobe, or a pure-Python parser per format),
  which is a dependency and a decode-untrusted-input surface — worth it only if
  "the sample must be 30-120 seconds" becomes a product rule rather than a guess.

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

## 2026-08-24 — Routing answers only the instant it was asked about
- **Partly addressed 2026-08-25 (Phase 5).** Of the two shapes named below, the
  product chose **record the unmet request**: a `preferred_teacher` request that
  is refused for capacity or availability creates a `TeacherWaitlist` entry and
  reports it inside `NoCapacity.considered`. What is *not* addressed is the
  auto-routing path — `route_session()` without a `preferred_teacher` still
  raises `NoCapacity` for the one instant it was asked about, proposes no
  alternate time and creates no waitlist entry (there is a test saying so:
  `test_waitlist_routing.py::test_the_normal_refusal_creates_no_waitlist_entry`).
  So the "candidate alternate slots" half of the entry below is still open, and
  a student who asks for an hour nobody has declared still just gets a 409.
- **What was skipped:** Any search for a *different* time. `route_session` takes one
  `start_time_utc`, tests every candidate against exactly that instant, and raises
  `NoCapacity` if none passes. It never walks forward through the teacher's declared
  hours to find the 10:00 slot that would have worked when 09:00 was full, and the
  `considered` payload names who declined rather than when they were free. The one
  exception is deliberate and bounded: step 1 will seat a student in a cohort whose
  `schedule_start_utc` is within `COHORT_START_TOLERANCE` (±2h) of the request, which
  is what the spec's "reasonably close to the requested window" buys.
- **Why:** The Phase 4 spec's step 4 is explicit that a clear failure is correct
  behaviour, not a bug to route around, and "propose an alternative" is a different
  feature from "decide who teaches this". Picking a substitute time silently is also
  the one outcome a parent cannot check — a student turning up an hour late to a
  session they never agreed to is worse than an honest 409.
- **Real fix:** Belongs to the preferred-teacher waitlist phase, which is the natural
  home for "we could not seat you now, here is what we can do". Two shapes to decide
  between when we get there: return candidate alternate slots alongside the refusal
  (the student picks, so nothing is silent), or record the unmet request and notify
  when capacity appears. The refusal payload should extend `considered` rather than
  invent a second reporting shape — same note as in learnings.md.
- **Revisit when:** The alternate-slots half, if the 409 rate on *auto-routed*
  requests shows students asking for hours nobody has declared. The waitlist half
  is done for preferred-teacher requests.

## 2026-08-25 — A preferred-teacher request is always 1:1, never a cohort seat
- **What was skipped:** Any cohort awareness on the preferred-teacher path.
  `routing._resolve_preferred()` passes no `cohort` to `_candidate()`, so naming a
  teacher produces a 1:1 booking even when that teacher has an open cohort for
  that level starting at exactly the requested time. Promotion behaves the same
  way.
- **Why:** The Phase 5 spec asks for exactly this, and asks for it to be logged
  here so it does not read as an oversight later: "A parent naming a specific
  teacher wants that teacher, not a seat in a class." Folding the two together
  silently would satisfy a preference with something adjacent to it, which is the
  failure mode the whole phase exists to prevent.
- **Consequence now:** A 1:1 preference against a teacher who is running a cohort
  at that instant is *refused* — the seat bookings and the 1:1 session really do
  clash (`test_waitlist_routing.py::test_a_one_to_one_preference_clashes_with_the_teachers_own_cohort`),
  so the family is waitlisted for a slot the teacher is technically teaching in.
  That is the honest answer under the current rule, but it is a confusing one.
- **Real fix:** Decide on purpose whether "I want Ustadh" ever means "seat me in
  Ustadh's class". If yes, it is a distinct feature: an explicit
  `accept_cohort_seat` flag on the request, or offering the seat in the refusal
  payload for the family to accept — not a silent widening of the current rule.
- **Revisit when:** A real cohort runs and a family names its teacher. Likely the
  same conversation as the recurring-cohort entry above, since a weekly beginner
  class is when this collision stops being hypothetical.

## 2026-08-25 — Cancelling a promoted session leaves the waitlist entry closed
- **What was skipped:** Any link between cancelling a promoted `Booking` and
  reopening the `TeacherWaitlist` entry that produced it. `is_open` reads
  `fulfilled_booking_id is None` and nothing else, so a cancelled session leaves
  the entry stamped and closed.
- **Why:** Asked rather than guessed, because two models are equally defensible
  and one of them means hooking Phase 3's already-committed `Booking.cancel()`.
  Product owner chose **promotion is one-way**: the entry is the permanent record
  of "asked, and was granted a session", and a cancellation afterwards is a new
  conversation rather than a reinstatement.
- **Consequence now:** The family drops out of the lead's queue holding no
  session, and `/waitlist/mine/` shows them `status: "fulfilled"`. Re-asking
  works — the partial unique constraint is scoped to *open* entries — but creates
  a fresh row, so they lose their original `requested_at` and `priority` and
  queue behind anyone who has been waiting since. Nobody is notified either way,
  since notification is the entry below. There are tests
  (`test_waitlist_routing.py::CancellingAPromotedSessionDoesNotReopenTheEntryTests`)
  so a later phase has to change this on purpose.
- **Real fix:** If reinstatement is wanted, the cleanest form is a `cancel()`
  hook (or a `post_save` receiver) that clears `fulfilled_booking` and leaves
  `requested_at`/`priority` untouched, so the family resumes their place — plus a
  decision about whether a *lead-initiated* cancellation and a
  *family-initiated* one should behave the same, which they probably should not.
- **Revisit when:** The first promoted session is cancelled. It is a data-quality
  problem before it is a fairness one, exactly like the cohort-seat entry above.

## 2026-08-25 — Nothing notifies a waitlisted family when a slot opens
- **What was skipped:** Any automatic offering. `TeacherWaitlist.notified` exists
  and **nothing in the codebase ever writes it**; a lead reading
  `/waitlist/for-teacher/` and calling `/promote/` by hand is the entire
  fulfillment mechanism.
- **Why:** The Phase 5 spec puts it explicitly out of scope, and it is not a
  shortcut so much as a different piece of work: "tell the next person in line
  the moment a slot frees up" needs a trigger (a cancellation, a widened
  availability window), a background worker, and a delivery channel — none of
  which exist yet.
- **Consequence now:** A slot can open and stay open with a family waiting for
  it, indefinitely, unless the lead happens to look. `notified` reading `False`
  on every row means nothing today, which is worse than the field being absent —
  a later reader could easily take it as "nobody has been told yet" rather than
  "this is never set".
- **Real fix:** A phase of its own. `Booking.cancel()` and availability edits are
  the triggers; `TeacherWaitlist.open_for_teacher()` is already the queue in the
  right order, so the missing pieces are the worker, the delivery (email needs
  the backend that registration is also waiting on), and a decision about whether
  an offer holds the slot for a while or is first-come-first-served.
- **Revisit when:** The first waitlist entry is real. Until then the lead is the
  scheduler and knows their own queue.

## 2026-08-25 — A sub-teacher cannot see who is waiting for them
- **What was skipped:** Any teacher-facing view of the waitlist.
  `/waitlist/for-teacher/?teacher_id=` is `IsLeadTeacher`, so a sub-teacher
  cannot read their own queue even though the entries name them.
- **Why:** It is a real decision rather than boilerplate — the queue exposes
  other families' requests, timing and `priority` to somebody who cannot act on
  any of them, since promotion is lead-only for the same reason opening a cohort
  is (it commits a teacher's time). Widening the read without widening the write
  would mostly create the ability to be frustrated.
- **Real fix:** If wanted, a teacher-scoped variant that returns only entries
  naming the caller, and a decision about whether a sub-teacher may promote from
  it. Note the shape of the existing view makes this cheap: the queryset is
  already `open_for_teacher(teacher_id)`, so the change is a permission class
  plus forcing `teacher_id` to the caller.
- **Revisit when:** A sub-teacher who isn't us asks "who is waiting for me". Same
  trigger as the availability-editor entry, and probably the same piece of work.

## 2026-08-25 — Parents cannot read their child's pricing agreement
- **What was skipped:** Parent access to `/api/pricing/agreements/mine/`, which
  is `IsStudent` only (`pricing/permissions.py`).
- **Why:** The spec's surface says "student sees their own active agreement", and
  who in a family may see money is a decision about a family, not boilerplate. A
  parent is usually the one paying, which argues for widening it — and is exactly
  why it was not widened silently.
- **Consequence now:** An asymmetry inside one phase, and a deliberate one:
  `/api/scheduling/waitlist/mine/` **was** widened to linked parents on the same
  day (product owner's call, 2026-08-25), because a parent can create a waitlist
  entry through `/route/` and a requester who cannot read their own request back
  is a hole. Pricing has no equivalent parent-write path, so nothing there is
  broken by leaving it — it is narrow, not inconsistent.
- **Real fix:** Widen to `IsStudentOrParent` scoped through `ParentLink`, exactly
  as `MyWaitlistListView` and `BookingCancelView` scope theirs. The `notes` field
  must stay absent for both (it is the lead's private reasoning), which
  `MyPricingAgreementSerializer` already guarantees by omission rather than by
  conditional.
- **Revisit when:** The payments phase, which is when a parent actually needs to
  see a rate to pay it. Deciding it earlier would be deciding it twice.

## 2026-08-25 — `PricingAgreement` records no timestamp
- **What was skipped:** Any `created_at` / `agreed_at` on `PricingAgreement`.
  Ordering is `["-pk"]`, so "newest first" is really "highest primary key first".
- **Why:** The spec's field list does not include one, and this phase deliberately
  does not add fields it was not asked for beyond the two on `TeacherWaitlist`
  that were explicitly approved. Insertion order and chronological order cannot
  disagree on an autoincrement key, so the history reads correctly today.
- **Consequence now:** A pricing history can say *what* was agreed and *who*
  approved it, but not *when* — which is half of what makes a financial record
  auditable, and the half nobody can reconstruct later. `TeacherWaitlist` has
  `requested_at`; the agreement it might justify has nothing.
- **Real fix:** An `auto_now_add` field plus a migration, and switch `Meta.ordering`
  to `["-created_at", "-pk"]`. Cheap, and cheaper before there are rows worth
  dating.
- **Revisit when:** The payments phase reads these rows, or the first time somebody
  asks when a rate changed. Whichever comes first.

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
  candidate set in SQL and compares ends in Python. *(The SQLite half of that
  reasoning expired on 2026-08-26; the rest still holds — the comparison is still
  in Python, because moving it is the fix below rather than a side effect of
  changing database.)*
- **Real fix:** On Postgres, a `tstzrange` generated column plus an
  `ExclusionConstraint` on `(teacher, range)` where `status='scheduled'` — which
  makes the rule race-proof *in the database*, rather than by agreement between
  writers. That also retires `TeacherBookingLock` and the `transaction_mode`
  setting the mitigation below depends on. **Phase 4 caveat:** the constraint must
  carry the same exemption `clashing_bookings()` now has — seats sharing a
  `cohort_id` do not clash, because a group class is one teacher teaching once. A
  constraint without it breaks every cohort the moment it is applied.
- **Revisit when:** ~~The Postgres move.~~ **The Postgres move happened
  (2026-08-26, Phase 6) and this entry deliberately survived it.** The phase spec
  asked whether `TeacherBookingLock` was still required, and the answer is yes,
  with a test rather than an opinion:
  `test_concurrency.LockIsStillRequiredOnPostgresTests` forces the interleaving
  with the lock patched out and reproduces the double booking, because READ
  COMMITTED does not stop a check-then-insert race and there is no constraint on
  `Booking` to refuse the second write. A control test at the same slot, with
  nothing patched, produces one booking — so the difference is the lock and
  nothing else. Applying the exclusion constraint is a schema change to a Phase 3
  model plus the cohort exemption above, which is a piece of work in its own right
  and not a hardening step. Next revisit: when write contention on one teacher
  becomes a measured problem, or when someone adds a new booking writer and
  forgets to take the lock — the weakness below that the constraint removes.
- **Mitigation now (Phase 3.5, updated Phase 6):** Creating a booking holds a
  per-teacher `TeacherBookingLock` row inside `transaction.atomic()`, so the
  overlap check and the INSERT are one atomic unit and a second request for the
  same slot is refused by `clean()` instead of committing. **What changed in Phase
  6 is how the row is held.** On SQLite it had to be a *write* — bumping
  `revision` — because SQLite has no row locks and Django's SQLite backend sets
  `has_select_for_update = False`, making `select_for_update()` there a silent
  no-op that reads as a fix and holds nothing. That also depended on
  `transaction_mode: IMMEDIATE`, without which SQLite took its write lock too late
  and the pair deadlocked into "database is locked". On PostgreSQL acquisition is
  an explicit `SELECT ... FOR UPDATE`, the `transaction_mode` option is gone, and
  `revision` is now purely diagnostic. Tests
  (`scheduling/tests/test_concurrency.py`) fail if the lock stops blocking, stops
  releasing, or stops being taken by either writer.
  Two things it does *not* do: it only guards creation, since no supported
  operation moves an existing booking onto a new slot (reschedule is
  cancel-and-rebook), and it serialises through a row rather than the database,
  so it is only as good as every writer remembering to take it — which is the
  precise weakness the exclusion constraint above removes.


## 2026-08-22 — `SECRET_KEY` and `DEBUG` are hardcoded
- **Resolved 2026-08-26 (Phase 6).** `DEBUG` now defaults to **False** — the
  inversion is the point, since development opting in is safe and production
  remembering to opt out is not. `DJANGO_PRODUCTION=1` is an explicit flag that
  both requires a real configuration (a non-development `DJANGO_SECRET_KEY` of at
  least 50 characters, explicit `DJANGO_ALLOWED_HOSTS` with no wildcard default,
  and `DJANGO_STORAGE_BACKEND=s3`) and switches on the HTTPS settings: HSTS with
  subdomains and preload, secure and httponly cookies, `SECURE_SSL_REDIRECT`, the
  proxy SSL header, nosniff, a referrer policy, and `X_FRAME_OPTIONS=DENY`.
  `check --deploy --fail-level WARNING` passes with no warnings and no documented
  exceptions. Kept here for the reasoning, not as outstanding work.
- **What was skipped:** Forcing these to be set from the environment.
- **Why:** Keeps a fresh clone runnable with no setup.
- **Real fix:** Raise on a missing `DJANGO_SECRET_KEY` when `DEBUG` is False,
  and add the standard production security settings (HSTS, secure cookies,
  `SECURE_SSL_REDIRECT`).
- **Revisit when:** First deploy to anything reachable from the internet.
- **One deliberate difference from the "real fix" above:** the gate is
  `DJANGO_PRODUCTION`, not `not DEBUG`. Tying HTTPS redirects to `DEBUG=False`
  would switch them on for the test suite and every local management command,
  against a server with no TLS. A staging box behind TLS gets the same settings
  without having to pretend to be production in any other respect.

## 2026-08-26 — Secret rotation is manual and unversioned
- **What was skipped:** Any support for rotating `SECRET_KEY` without
  invalidating things signed with the old one.
- **Why:** Nothing needed it before Phase 6. Now two things are signed with it:
  session cookies (Django's own, and this is an API — clients hold DRF tokens,
  which are database rows and survive a rotation) and the placement-audio access
  tokens added this phase.
- **Real fix:** `SECRET_KEY_FALLBACKS`, which Django checks when verifying but
  never uses to sign. A rotation then means: new key in `SECRET_KEY`, old key in
  `SECRET_KEY_FALLBACKS`, and drop it once the longest-lived signature has expired.
- **Revisit when:** The first rotation is actually needed. The blast radius today
  is small and self-healing — an audio URL is valid for five minutes, so a
  rotation breaks at most five minutes of in-flight playback and nothing durable.
  Worth wiring before a rotation is done under pressure, i.e. before a suspected
  key compromise rather than after one.

## 2026-08-30 — A wrong finalized payout has no correction path
- **What was skipped:** Any way to reverse, amend or credit a payout once it is
  finalized. `TeacherPayout.clean()` refuses every write to a finalized row, the
  admin declines to offer the form, and there is no `reversed` state.
- **Why:** The Phase 8 spec names corrections as an explicit later phase, and the
  alternative was worse: an editable financial record is one where "what did we
  pay in August" has no single answer. Refusing the edit keeps the question
  answerable while the correction workflow is still undesigned.
- **Real fix:** A correcting entry rather than an edit — a second payout row
  linked to the first with a reason, so the history reads as "paid 5000, then
  adjusted by -500" instead of silently becoming 4500. That needs a product
  decision about who may issue one and whether a teacher sees the adjustment
  separately.
- **Revisit when:** The first real payout is wrong. Until money has actually moved
  the practical fix is to delete the *generated* draft and regenerate, which is
  supported.

## 2026-08-30 — One hardcoded currency
- **What was skipped:** Multi-currency support. `payouts.models.PAYOUT_CURRENCY`
  is the constant `"NGN"`, stamped onto every record.
- **Why:** Currency conversion is explicitly out of scope for Phase 8, and a
  per-teacher currency field with no conversion rule would be a way to produce
  statements that cannot legitimately be totalled.
- **Real fix:** The code is already shaped for it — the currency is *stored* on
  each payout rather than assumed at read time, so historical rows keep their
  meaning. Adding a second currency means deciding where the rate comes from,
  whether statements may mix currencies (probably not: one statement per currency),
  and what `Statement.total_amount` means if they do.
- **Revisit when:** A teacher is paid in anything other than naira.

## 2026-08-30 — Cohort payout deduplication inherits the single-session assumption
- **What was skipped:** Any per-session identity for a cohort. "One payout per
  cohort session" is enforced as a partial unique constraint on `cohort_id`, which
  is exact only because a `Cohort` has a single `schedule_start_utc`.
- **Why:** It is the same assumption `scheduling.weekly_committed_minutes` already
  makes for capacity, and Phase 8 was explicitly told not to invent a new
  attendance or session model. Two deduplication rules with two different ideas of
  "a session" would be worse than one shared assumption.
- **Real fix:** Whatever gives a recurring cohort its per-occurrence identity —
  most likely a `CohortSession` row that seats point at. Both the capacity
  calculation and this constraint then key on that instead of on `cohort_id`.
- **Revisit when:** Recurring group classes are implemented (the existing
  recurrence gap in this file). Doing it before then would be building the
  identity model this phase was told not to invent.

## 2026-08-30 — Payout listings are unpaginated, and generation is not serialized
- **What was skipped:** Two things a busier academy will want. `GET
  /api/payouts/lead/` returns every matching record in one response (the project
  has no `DEFAULT_PAGINATION_CLASS` at all, so this matches every other listing).
  And concurrent generation of the same period is *prevented* rather than
  *serialized*: the second run loses on the unique constraint, its whole
  transaction rolls back, and the API answers 409 telling the caller to repeat it.
- **Why:** Generation is a lead-only action that runs once a period, so the
  realistic race is a double-clicked button — for which "nothing was created,
  try again" is a correct and cheap answer. A `TeacherBookingLock`-style advisory
  lock would be the heavier fix and buys nothing until two people run payroll at
  once.
- **Real fix:** Pagination as a project-wide setting rather than per endpoint; and
  for generation, a period-scoped advisory lock following the
  `TeacherBookingLock` pattern, so the second run waits and then correctly finds
  nothing to do.
- **Revisit when:** A period covers enough sessions that the listing response is
  unwieldy, or payroll stops being one person's job.

## 2026-09-05 — A suspended member is told nothing, and can suspend themselves
- **What was skipped:** Any signal to someone whose organization membership was
  suspended. `GET /api/organizations/mine/` returns `[]` for them and the academy
  answers 403, so from the client's side a suspension is indistinguishable from
  never having been a member. Relatedly, nothing stops an admin from suspending
  *their own* membership through `PATCH .../memberships/{id}/` and losing their
  own management access; only the owner's row is protected.
- **Why:** Phase 1's rule is that an active membership is the only thing that
  grants tenant access, and the alternative — showing a suspended member their
  academy's name, slug and timezone — would have been the single hole in it.
  Telling them *why* needs somewhere to say it (notification, onboarding copy),
  and both are explicitly later phases. Self-suspension is a footgun rather than a
  security hole: an academy can never be fully locked out, because the owner's
  membership cannot be suspended by anyone.
- **Real fix:** A narrow "my access" endpoint that reports the caller's own
  membership rows including suspended ones, with no organization detail beyond the
  name — the same distinction `accounts` already draws between a user's own `/me/`
  and what other users may see. Refusing a self-directed suspension is a two-line
  object-level check whenever the product decides that is the desired behaviour.
- **Revisit when:** The onboarding phase gives the platform anywhere to explain an
  access change, or the first real academy suspends someone.

## 2026-09-05 — `Organization.is_active` is stored and read by nothing
- **What was skipped:** Any behaviour behind the field. The phase spec lists
  `is_active` among the organization's fields and says an inactive organization
  should not be deleted merely to disable access, but the tenant rule it also
  states is `organization access == active OrganizationMembership`, with nothing
  about the organization's own flag. So the column exists, defaults `True`, is
  read-only through the API, and no permission or queryset consults it.
- **Why:** Deciding that `is_active=False` revokes access for everyone inside an
  academy — owner included, who would then be unable to read the tenant they are
  supposed to be reactivating — is a product decision with a workflow attached, and
  organization deletion and settings are named later phases. Implementing it here
  would have meant inventing the workflow to go with it.
- **Real fix:** Either the academy-settings phase gives the flag meaning (most
  likely: non-owner access refused, owner retains read plus a reactivate action,
  enforced inside `active_membership()` so every domain inherits it at once), or
  the field is dropped in favour of whatever suspension model billing needs.
- **Revisit when:** Academy onboarding or settings lands — and before any later
  phase writes code that *assumes* the flag already gates access.

## 2026-09-05 — `OrganizationRole` has no `student` or `parent` value
- **What was skipped:** Any way to say "this person is a student of this academy" or
  "a parent of one". Phase 1's vocabulary is `owner`/`admin`/`staff`/`teacher`, and
  the assignable subset is `admin`/`staff`/`teacher`, so admitting a student today
  means giving them an authority role that overstates them. Phase 2's rules do not
  depend on which one — belonging is `status == active` and the account role supplies
  the rest — but the row still reads wrong, and the tests say so out loud with a
  `BELONGS` constant rather than hiding it.
- **Why:** Adding two choices to a shipped Phase 1 model is a change to the
  organization role vocabulary, which the phase spec never asks for and CLAUDE.md
  tells this phase not to redesign. It is also entangled with the question the
  onboarding phase owns anyway: *how* a student or parent enters an academy at all,
  which needs invitations, bulk import and a registration-with-organization-context
  decision that Phase 2 is explicitly forbidden from making.
- **Real fix:** Most likely `STUDENT` and `PARENT` added to `OrganizationRole` and to
  `ASSIGNABLE_ORGANIZATION_ROLES`, staying outside `MEMBERSHIP_MANAGER_ROLES` so they
  carry the least authority — an additive migration with no data change. Alternatively
  the role field narrows to *staff* authority only and participation moves to a
  separate field, which is a larger change and needs the onboarding phase's shape
  first.
- **Revisit when:** Academy onboarding lands, or the first real academy admits a
  student — whichever comes first. Do it before curriculum or scheduling tenancy
  writes code that filters on the organization role.

## 2026-09-05 — Teacher terms live in two tables and only the old one is read
- **What was skipped:** Any consolidation. `approved`, `max_weekly_hours` and
  `hourly_payout_rate` now exist on both `TeacherProfile` (global) and
  `OrganizationTeacherConfiguration` (per-academy), and every consumer still reads the
  global one: `bookable_teacher_error`, `specialty_error`, the `Booking` and
  `route_session` weekly caps, `lead_teacher`, `matching_sub_teachers` and
  `applicable_rate`. So an academy can approve a teacher through the new API and
  scheduling will still refuse to book them, and it can set a rate that payroll will
  not pay.
- **Why:** Deliberate, and the reason the migration is safe. Phase 2 may not rewrite
  scheduling or payout behaviour, so the new table had to be introduced without any
  domain switching over in the same change. The alternative — moving the fields — would
  have meant rewriting booking eligibility, routing, capacity and payroll at the same
  moment the model appeared.
- **Real fix:** Scheduling tenancy reads `approved`, `max_weekly_hours` and the
  specialty rule per organization once bookings know their academy; payout tenancy does
  the same for the rate. Each can backfill from the global profile in its own data
  migration. `TeacherProfile` then keeps `bio` and whatever remains genuinely global,
  or disappears into `User`.
- **Revisit when:** SaaS scheduling tenancy starts. Until then, treat the new table as
  storage: nothing operational depends on it, and the API documents that in its
  `help_text`.

## 2026-09-05 — A `teacher` membership does not require an account that can teach
- **What was skipped:** Validation on `OrganizationMembership` itself. A student or
  parent account can hold `role = teacher` in an academy; it grants the
  least-privileged authority there and nothing else, and the refusal happens only when
  someone tries to give that membership teaching terms.
- **Why:** Phase 1 kept the organization app from reading `User.role` at all, and
  enforcing the rule on the membership would have broken
  `OrganizationMembershipFactory`'s default plus a set of shipped Phase 1 tests —
  which makes it a change to a finished phase rather than a Phase 2 fix. The teaching
  identity itself *is* guarded, at `OrganizationTeacherConfiguration.clean()`, so the
  invariant the spec cares about holds.
- **Real fix:** Either the membership validates the pairing (a `clean()` that reads
  `TEACHER_ROLES`, plus a factory and test sweep), or the role vocabulary is reworked
  along with the `student`/`parent` gap above so the two decisions are made together.
  The second is probably better — they are the same decision.
- **Revisit when:** The `student`/`parent` role gap is resolved, or a real academy
  files a confusing membership.

## 2026-09-05 — A teacher cannot see what their academy pays them
- **What was skipped:** Any teacher-facing read of
  `/api/accounts/organizations/{id}/teacher-configurations/`. Owner and admin manage
  it; staff and teacher members get 403 on the whole surface, list included — so a
  teacher has no way to see their own approval, weekly cap or hourly rate for an
  academy.
- **Why:** The same narrower default Phase 1 chose for the membership directory: what
  an academy pays its teachers is not something an ordinary member reads by default,
  and a narrow rule can be widened safely later while the reverse leaks. Adding a
  self-read also needs a decision about *which* fields a teacher sees, which is a
  product question rather than a permission one.
- **Real fix:** A narrow route returning only the caller's own terms for one academy —
  the distinction `accounts` already draws between `/me/` and what other users may see.
  A filtered queryset plus one permission class, once the product decides whether a
  teacher sees their rate before payroll does.
- **Revisit when:** A teacher-facing client exists, or payout tenancy gives teachers a
  reason to check their rate.

## 2026-09-05 — Parent authorization in scheduling and assessment is still global
- **What was skipped:** Tenant-scoping the three places another domain checks a
  `ParentLink` to decide whether a parent may act for a child:
  `scheduling/serializers.py:63` (booking on a child's behalf),
  `scheduling/views.py:200` and `:444` (booking and waitlist visibility), and
  `assessment/views.py:143` (family progress). All four ask only "is this a real
  parent-child link", with no organization in the question.
- **Why:** Correct for now — those domains are not tenant-scoped at all yet, so there
  is no academy in scope to check against, and Phase 2 is forbidden from changing
  scheduling or assessment behaviour. The accounts side of the rule exists and is
  tested (`tenancy.children_in_organization()`), waiting for them.
- **Real fix:** When `Booking`, `Cohort` and `SessionAssessment` gain an organization,
  each of those checks becomes "linked *and* both active members here", reusing
  `children_in_organization()` or the membership helpers rather than re-deriving the
  rule.
- **Revisit when:** SaaS scheduling tenancy and assessment tenancy respectively — and
  before either ships, because a booking that knows its academy while its parent check
  does not is the exact shape of a cross-tenant leak.

## 2026-09-05 — Five permission modules still equate `User.role == lead` with academy authority
- **What was skipped:** The owner/lead migration. `curriculum/permissions.py`,
  `scheduling/permissions.py`, `pricing/permissions.py`, `assessment/permissions.py`
  and `payouts/permissions.py` each gate their privileged actions on
  `user.role == Role.LEAD`, which in a multi-tenant world means "any lead teacher
  anywhere", not "this academy's authority". `TeacherProfile.is_lead` mirrors the same
  field, and `routing.lead_teacher()` filters on both.
- **Why:** It is the single largest single-academy assumption in the repository and the
  spec names it as out of scope for Phase 2 — deliberately, because replacing it means
  deciding what `owner`, `admin` and `lead teacher` each authorize in every domain, and
  doing that at the same time as introducing account tenancy would make both
  unreviewable. `User.role` was left exactly as it was.
- **Real fix:** Each domain's privileged checks move to `active_membership()` plus
  `OrganizationMembership.role`, one phase at a time, and `Role.LEAD` narrows to what
  it actually describes — a teaching seniority — or disappears.
- **Revisit when:** The domains become tenant-scoped. Each phase should convert its own
  permission module rather than leaving a final sweep to do all five at once.
- **Partly addressed 2026-09-05 (SaaS Phase 3), for `curriculum` only.** `Role.LEAD` is
  still what `IsLeadTeacher` reads, but it is no longer used alone: every privileged
  curriculum endpoint pairs it with `IsOrganizationMember` and an academy-scoped
  queryset, so "a lead teacher somewhere" now authorizes nothing. Curriculum authoring
  moved off `User.role` entirely and onto `OrganizationMembership.role`
  (`CURRICULUM_MANAGER_ROLES`). The other four modules are untouched.

## 2026-09-05 — Teacher eligibility lives in two relations and only the old one is enforced
- **Resolved 2026-09-11 (SaaS Phase 4).** All scheduling logic (`Booking.clean()`,
  `Cohort.clean()`, `scheduling.routing.matching_sub_teachers`, and `route_session()`)
  now resolves teacher track eligibility exclusively through `curriculum.TeacherTrack`
  scoped to the booking's organization. `TeacherProfile.specialties` is no longer
  consulted for scheduling decisions. The legacy field is retained only for backwards
  compatibility until all consumers across remaining apps are audited.
- **What was skipped:** Retiring `TeacherProfile.specialties`. SaaS Phase 3 added
  `curriculum.TeacherTrack` — academy-scoped, keyed on `OrganizationMembership` — and
  left the global many-to-many in place beside it. Nothing reads the new one yet:
  `scheduling.models.specialty_error`, `Booking.clean()`, `Cohort.clean()` and
  `scheduling.routing.matching_sub_teachers` all still filter on
  `teacher_profile__specialties`.
- **Why:** The spec forbids it in as many words — "do not remove it until SaaS Phase 4
  has a safe migration path" — and the reason is sound: the readers are the routing and
  capacity logic Phase 3 is explicitly told not to rewrite. Swapping the relation under
  them while also introducing curriculum tenancy would make both changes unreviewable.
- **Consequence now:** a teacher's *bookable* tracks are still global, so a teacher
  recorded against Academy A's `tajweed` can be booked for Academy B's level in the same
  track. That is not a new leak — scheduling has no tenant boundary at all yet, which is
  what Phase 4 is for — but it is the one place where the two relations visibly disagree,
  and the new one is the correct answer.
- **Real fix:** SaaS Phase 4 points the scheduling readers at
  `TeacherTrack.objects.in_organization(...).active()`, resolving the teacher's
  membership from the booking's academy, then drops `specialties` in a migration that
  asserts every remaining row has an equivalent.
- **Revisit when:** SaaS Phase 4 (scheduling tenancy) starts. It is the first thing that
  phase should do, before touching booking rules, because the two relations diverge
  further with every academy that configures its own teachers.

## 2026-09-05 — Legacy participants are admitted as `staff` because there is no student role
- **What was skipped:** Giving `OrganizationRole` a value that means "a student of this
  academy". The Phase 3 backfill admits the users who already hold legacy curriculum
  data, and a student or parent lands on `staff` — the same stand-in Phase 2's tests
  settled on and the same gap already recorded above ("`OrganizationRole` has no
  `student` or `parent` value").
- **Why:** Inventing the role value is a Phase 1 model change with its own permission
  consequences in five apps, and the alternative — not admitting legacy students at all —
  would have made their existing placements unreadable, which the spec forbids.
  `staff` carries no authority over memberships, curriculum or teaching terms today, so
  the choice is harmless *now*.
- **Consequence now:** production data will contain `staff` memberships that actually
  describe students and parents, so any future rule that grants `staff` something a
  student should not have would silently grant it to them.
- **Real fix:** add `student` and `parent` to `OrganizationRole`, then a data migration
  that re-roles memberships from `User.role` — which is exactly the migration the
  existing `OrganizationRole` entry describes. Anything that widens `staff` before then
  must check `User.role` as well.
- **Revisit when:** Before any phase grants `staff` a new capability, and at the latest
  in the tenant security audit (SaaS Phase 11).

## 2026-09-05 — Deleting an academy would cascade away its curriculum and placement history
- **What was skipped:** Deciding what happens to an academy's curriculum when the
  academy is deleted. `Track.organization` is `CASCADE`, matching
  `OrganizationMembership.organization`, and `Level` and `PlacementResult` already
  cascade from `Track` — so removing an `Organization` row would take its syllabus, its
  students' placements and their recorded levels with it.
- **Why:** There is no organization-deletion workflow anywhere in the product — Phase 1
  named it an explicit later decision and nothing has been built since — so the
  cascade is a statement about ownership rather than a live code path. `PROTECT` would
  have been the other defensible choice, but it would have been a Phase 1 decision
  reversed by a Phase 3 field.
- **Consequence now:** nothing, until someone deletes an organization from the admin or
  a shell. There is no endpoint that can.
- **Real fix:** whatever phase builds academy closure decides between soft-deletion
  (`Organization.is_active`, which is stored and read by nothing — see its own entry)
  and an explicit export-then-purge, and changes these `on_delete` policies
  deliberately rather than discovering them.
- **Revisit when:** Academy onboarding (SaaS Phase 8) or settings (SaaS Phase 9) puts a
  destructive action anywhere near an `Organization` row.

## 2026-09-05 — Assessment, pricing and scheduling still resolve curriculum globally
- **Partly resolved 2026-09-11 (SaaS Phase 4).** Scheduling endpoints and serializers
  now scope all curriculum references (`Level`) to the organization resolved from the URL
  via `AcademyScopedSerializerMixin` (`levels_in(organization)`). Pricing and assessment
  remain global until SaaS Phase 5 and SaaS Phase 6 respectively.
- **What was skipped:** Narrowing the curriculum lookups those apps make.
  `scheduling/serializers.py` and `pricing/serializers.py` accept
  `Level.objects.all()`, and `assessment/serializers.py` and `assessment/views.py`
  accept `Track.objects.all()` — so a caller who knows an id can name another academy's
  level or track in a booking, an agreement, a rubric or a progress query.
- **Why:** The spec confines Phase 3 to curriculum and forbids tenant-migrating those
  domains, and each of them needs its own academy resolved from its own route before a
  narrowed queryset means anything. Adding a filter with no organization to filter on
  would have been a change that looks like a fix and is not one.
- **Consequence now:** the same cross-academy reachability those apps already have —
  they have no tenant boundary yet — but with academy-owned curriculum on the other end
  of it, so the mismatch is now visible in a way it was not before.
- **Real fix:** each domain's tenancy phase adds the `organizations/{id}/` route
  prefix, resolves the membership, and narrows its curriculum fields to
  `Track.objects.filter(organization=...)` / `Level.objects.filter(track__organization=...)`
  — the two helpers `curriculum/serializers.py` already exposes as `tracks_in()` and
  `levels_in()`.
- **Revisit when:** SaaS Phase 5 (pricing — resolved 2026-09-11) and SaaS Phase 6 (assessment). The
  tenant security audit should confirm none were missed.

## 2026-09-05 — Curriculum authoring is owner/admin only, which may be too narrow
- **What was skipped:** Letting a `teacher` membership create or edit tracks and levels.
  The spec's permission table marks it "policy-dependent"; the product owner chose owner
  and admin (2026-09-05).
- **Why:** It is the narrower default, and a rule that can be widened later without a
  migration or a security review — which the reverse is not. Phase 1 made the same call
  for the membership directory.
- **Consequence now:** in an academy whose lead teacher is not also its owner or an
  admin, the person who actually designs the syllabus cannot enter it, and an
  administrator has to. Small academies will feel this first.
- **Real fix:** either widen `CURRICULUM_MANAGER_ROLES` to include
  `OrganizationRole.TEACHER`, or add a per-academy setting once academy settings exist
  (SaaS Phase 9) so each academy chooses. The tests assert the current answer, so
  widening it has to be deliberate.
- **Revisit when:** The first academy onboards a lead teacher who is not its owner.

## 2026-09-11 — `TeacherProfile` retains legacy fields while waiting for Payout tenancy
- **What was skipped:** Removing `TeacherProfile` entirely or dropping its legacy fields (`approved`, `max_weekly_hours`, `specialties`, `hourly_payout_rate`).
- **Why:** SaaS Phase 4 migrated scheduling authority to `OrganizationTeacherConfiguration` and `TeacherTrack`. However, `hourly_payout_rate` is still read by `payouts` (SaaS Phase 7), and removing legacy fields prematurely would break unmigrated apps and existing database fixtures.
- **Real fix:** In SaaS Phase 7 (payout tenancy), migrate `hourly_payout_rate` to `OrganizationTeacherConfiguration`. Afterwards, audit remaining consumers (e.g. `bio`, which is personal and global) and deprecate or remove unused columns.
- **Revisit when:** SaaS Phase 7 (payout tenancy).

## 2026-09-11 — No teacher-facing multi-tenant availability editor
- **What was skipped:** Any teacher-facing API or UI to view and edit availability across multiple academies.
- **Why:** Phase 4 focused on backend tenancy, data integrity, and scoping existing endpoints. Availability write endpoints were already out of scope in Phase 3/3.5, and multi-academy availability editing requires handling timezone conversions, midnight-splitting, and cross-academy conflict visualization.
- **Real fix:** A teacher-authenticated multi-tenant availability API that accepts local teacher windows, displays commitments across all joined academies, and automatically splits and stores UTC windows per academy.
- **Revisit when:** Academy settings (SaaS Phase 9) or teacher dashboard frontend.

## 2026-09-11 — Automated multi-tenant waitlist notification is deferred
- **What was skipped:** Background worker to automatically notify waitlisted students across academies when a teacher's slot opens up or when new availability is created.
- **Why:** Out of scope for Phase 4; notification infrastructure, background task queue (Celery/RQ), and email templates have not yet been introduced.
- **Real fix:** Add a background worker triggered by booking cancellation or availability expansion that scans `TeacherWaitlist.open_for_teacher()` in the corresponding organization and sends email/push alerts.
- **Revisit when:** Notification / communications infrastructure phase.

## 2026-09-11 — Parent view of student pricing agreements is deferred
- **What was skipped:** Allowing parents to view pricing agreements for their linked children under `/api/pricing/organizations/<organization_pk>/agreements/mine/` (or a dedicated `/children/` pricing endpoint).
- **Why:** In MVP Phase 5, `/mine/` strictly serves the student user. Parents can link to students across academies, and adding family hierarchy pricing lookup requires family billing permissions and guardian-scope authorization.
- **Real fix:** Add a dedicated guardian-facing pricing view `/api/pricing/organizations/<organization_pk>/agreements/children/` verifying `ParentLink`, active student membership, and active parent membership in that academy.
- **Revisit when:** Family portal & guardian accounts phase or SaaS Phase 8 (Billing).

## 2026-09-11 — Billing, subscriptions, wallets, invoices, and payouts deferred
- **What was skipped:** Any monetary transactions, payment processing, invoice generation, subscription management, student wallets, or teacher payout calculation based on agreed rates.
- **Why:** SaaS Phase 5 is strictly scoped to rate negotiation and pricing tenancy. Financial integrations are explicitly separated into SaaS Phase 7 (payouts) and SaaS Phase 8 (billing/payments).
- **Real fix:** Introduce Stripe / payment gateway webhooks, invoice generation, balance accounting, and payout reconciliation in dedicated financial phases.
- **Revisit when:** SaaS Phase 7 (Teacher Payouts) and SaaS Phase 8 (Billing & Payments).

