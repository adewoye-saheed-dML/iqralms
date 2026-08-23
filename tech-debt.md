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
- **Revisit when:** Before the first real student uploads a sample. This is the
  blocker of the three audio entries here — the other two are cleanup.

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

## 2026-08-23 — Replaced placement audio is orphaned on disk
- **What was skipped:** Deleting the previous file when a student re-submits a
  placement, or when a `PlacementResult` row is deleted.
- **Why:** `PlacementResult.submit()` reassigns `audio_sample` to implement the
  spec's "update the existing row" rule; Django has not auto-deleted the
  displaced file since 1.3. Verified: after a re-submit both files remain in
  `MEDIA_ROOT`, and a beginner skip clears the field while leaving the file.
  Deleting user-uploaded data is exactly the kind of call CLAUDE.md says to ask
  about rather than decide silently, so nothing deletes anything for now.
- **Real fix:** Decide the retention rule first (keep every sample as an audit
  trail of how a student was levelled, or keep only the current one), then
  implement it — `post_delete`/`pre_save` signals if only the current sample
  matters, or an explicit `PlacementSample` history table if the old ones are
  worth keeping. Student deletion cascades the row, so that path needs the same
  answer.
- **Revisit when:** Whichever comes first — the storage bill, or the first
  data-deletion request. Moving to object storage does not fix this, it just
  moves where the orphans pile up.

## 2026-08-22 — `SECRET_KEY` and `DEBUG` have development defaults
- **What was skipped:** Forcing these to be set from the environment.
- **Why:** Keeps a fresh clone runnable with no setup.
- **Real fix:** Raise on a missing `DJANGO_SECRET_KEY` when `DEBUG` is False,
  and add the standard production security settings (HSTS, secure cookies,
  `SECURE_SSL_REDIRECT`).
- **Revisit when:** First deploy to anything reachable from the internet.
