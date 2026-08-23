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
- **Revisit when:** Phase 4. Until then a parent can book a hifz teacher for an
  Arabic level and nothing objects.

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
- **What was skipped:** Any "start_time_utc must be in the future" rule.
- **Why:** The spec doesn't ask for one, and the rules it does ask for
  (declared hours, no overlap) are indifferent to when "now" is. Availability is
  a weekly rule, so last Monday 09:00 is as inside the window as next Monday's.
- **Real fix:** A `clean()` rule rejecting a new booking whose start is in the
  past, with a small grace period so a booking made at 09:00:01 for 09:00 isn't
  refused. It has to be creation-only, like the availability check, or existing
  bookings become unsaveable the moment they end.
- **Revisit when:** Before real students book. Two consequences make this more
  than cosmetic: the overlap rule only considers *scheduled* bookings, so a
  completed session's slot is re-bookable (proven by test), and backdated
  bookings would corrupt any attendance or payout figure computed from history.

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
  makes the rule race-proof, not merely correct. Today two simultaneous
  requests for the same slot can both pass `clean()` and both commit.
- **Revisit when:** The Postgres move, or sooner if two people ever book the
  same teacher at the same second. The candidate query is bounded by
  `LONGEST_POSSIBLE_BOOKING`, so this is a correctness-under-concurrency issue,
  not a performance one.

- **What was skipped:** Forcing these to be set from the environment.
- **Why:** Keeps a fresh clone runnable with no setup.
- **Real fix:** Raise on a missing `DJANGO_SECRET_KEY` when `DEBUG` is False,
  and add the standard production security settings (HSTS, secure cookies,
  `SECURE_SSL_REDIRECT`).
- **Revisit when:** First deploy to anything reachable from the internet.
