# Phase 6 — Production Hardening

## Goal

Remove the small set of MVP infrastructure shortcuts that are no longer appropriate
as the academy approaches real users, without beginning the next product domain.

This phase is deliberately bounded. It does **not** redesign curriculum,
scheduling, routing, cohorts, pricing, or waitlist behaviour. It makes the existing
backend safer and more representative of production conditions so the next product
phase can build on a stable foundation.

## Why this phase now

Phases 1–5 established the product's core domain model and routing flow. The codebase
now has booking concurrency logic, teacher capacity rules, private business data,
and placement recordings. Those features are still running on development-oriented
infrastructure:

- SQLite is the runtime database.
- Placement audio uses local `MEDIA_ROOT` storage and is served by Django in DEBUG.
- Placement uploads do not validate content type, size, or file signature.
- Development-safe settings are still the default configuration path, without a
  deliberate production configuration boundary.

These are no longer useful product shortcuts once real student data is introduced.
The phase therefore hardens the infrastructure, then stops.

## Scope

### 1. PostgreSQL as the canonical database

Replace the current SQLite runtime configuration with PostgreSQL selected through
`DATABASE_URL`.

Requirements:

- Development, test, and production settings must all support PostgreSQL.
- The application must no longer depend on SQLite-specific transaction settings
  (`transaction_mode`, SQLite busy timeout, or SQLite locking behaviour) for booking
  correctness.
- Existing booking concurrency guarantees must remain true on PostgreSQL.
- The dedicated `TeacherBookingLock` mechanism may be retained only if tests show it
  is still required for the application's chosen PostgreSQL concurrency strategy.
  Do not keep it merely because it exists today.
- The database configuration must fail clearly when a required production
  `DATABASE_URL` is missing or invalid.
- A fresh clone must still have a documented development path to start a local
  PostgreSQL database, preferably through Docker Compose or an equivalent explicit
  setup. Do not silently fall back to SQLite.

Do not redesign the application's models merely to make the database migration
convenient. Preserve the current schema unless PostgreSQL requires a concrete fix.

### 2. Private placement-audio storage

Move `PlacementResult.audio_sample` away from local public media storage.

Requirements:

- Use `django-storages` with an S3-compatible private bucket.
- Objects must not be publicly readable.
- The API must not expose a permanent public media URL.
- A lead reviewing a placement must receive a short-lived signed URL or equivalent
  private access mechanism for the audio sample.
- A student may read their own placement record, but must not gain access to another
  student's recording through the API.
- Existing placement cleanup behaviour must remain correct when a placement is
  replaced, skipped, deleted, or cascaded.
- The storage backend must be injectable/configurable by environment so tests do not
  require a real cloud bucket.
- Development must have a clearly documented private-storage path. A local fake or
  S3-compatible service is acceptable for tests/development, but do not restore the
  old public `MEDIA_URL` behaviour as the application path.

Do not add placement-sample history in this phase. The current product decision is
still "keep only the current sample".

### 3. Placement-upload validation

Validate uploaded placement audio before it is persisted.

Requirements:

- Enforce an explicit maximum upload size.
- Enforce an allowlist of supported audio formats.
- Validate both the declared filename/content type and the file's actual signature
  (magic bytes) where practical.
- Reject mismatched or malformed files.
- Reject clearly non-audio payloads renamed with an audio extension.
- Do not rely solely on the browser/client `Content-Type` header.
- Keep validation at the serializer boundary for clear API errors, with any model or
  storage-level safeguard that is necessary to prevent bypasses.
- Tests must cover valid audio, an invalid extension/type, a spoofed MIME type, an
  oversized upload, and the beginner-skip path.

The exact maximum size and supported formats are implementation values to choose
reasonably for short Quran recitation samples. Record the chosen values in the code
and tests rather than leaving them implicit.

### 4. Production configuration boundary

Separate development defaults from production-required settings.

Requirements:

- `DEBUG` must default to `False` unless explicitly enabled for local development.
- Production must require a non-development `DJANGO_SECRET_KEY`.
- Production must require explicit `DJANGO_ALLOWED_HOSTS`.
- Production must not serve user-uploaded media through Django's development media
  route.
- Add the standard secure-cookie, CSRF, HSTS, proxy/SSL, and clickjacking settings
  needed for a deployment behind HTTPS, gated by an explicit production setting.
- Keep the settings module simple; do not create a large configuration framework.
- Environment parsing should be explicit and typed where values are not strings.

### 5. Regression protection

This phase is successful only if the existing product behaviour remains intact.

At minimum, the full suite must still cover:

- account registration and authentication;
- parent/student permissions;
- placement submit/review;
- availability and timezone handling;
- booking validation and cancellation;
- booking concurrency;
- cohort routing;
- capacity routing;
- preferred-teacher waitlists;
- pricing agreements.

The production-hardening changes must not silently alter any of those business rules.

## Non-goals

Do **not** implement any of the following in Phase 6:

- payments or charging;
- payouts to teachers;
- recurring cohorts;
- assessment/rubric models;
- automatic waitlist notifications;
- alternate-time recommendation;
- teacher-quality ranking;
- new student/teacher UI;
- placement-sample history;
- broad refactoring of existing domain models;
- replacing DRF, dj-rest-auth, or the current authentication model.

Those are separate product/design decisions.

## Data migration and deployment notes

The implementation must include a safe path for existing local placement files during
migration to private storage.

For the current repository there may be development files under `MEDIA_ROOT`; the
phase must document whether they are intentionally discarded as development-only data
or migrated. Do not invent a production data migration for files that have never been
production data.

For database migration, the expected deployment path is:

1. configure PostgreSQL;
2. run migrations against a fresh PostgreSQL database;
3. run the full test suite on PostgreSQL;
4. run Django system checks with production settings;
5. only then consider the infrastructure ready for the next product phase.

## Configuration contract

Document the environment variables required by the application after this phase.
At minimum this includes:

- `DATABASE_URL`
- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- database SSL configuration if required by the deployment;
- object-storage bucket, region/endpoint, credentials, and private URL settings;
- any explicit production flag used to activate secure deployment settings.

Never commit credentials, real bucket names tied to private infrastructure, or a real
production secret.

## Acceptance criteria

1. A fresh PostgreSQL database accepts all migrations cleanly with
   `python manage.py migrate`.
2. The full test suite passes against PostgreSQL; no test requires SQLite.
3. Booking concurrency tests still prove that two competing writes cannot create an
   invalid double booking.
4. No application code relies on SQLite-only transaction configuration or SQLite
   locking semantics.
5. Placement audio is stored in a private object-storage backend; no placement
   response exposes a permanent public object URL.
6. An authorized lead can obtain a short-lived access URL for a placement sample.
7. An unauthorized user cannot retrieve another student's placement sample through
   any placement endpoint.
8. Placement upload validation rejects an oversized file, a disallowed format, and a
   spoofed/non-audio payload.
9. A valid supported audio sample still reaches the placement-review flow unchanged.
10. Replacing, deleting, skipping, and cascading deletion of placements still clean
    up the current stored object correctly after the storage change.
11. With production settings enabled, `DEBUG=False`, the development secret fallback
    is rejected, explicit hosts are required, and Django does not expose local media.
12. `python manage.py check --deploy` passes under the documented production
    configuration, except for warnings that are explicitly impossible to eliminate
    in the chosen deployment environment and documented in the phase commit.
13. `python manage.py makemigrations --check` passes.
14. The required environment variables and local PostgreSQL setup are documented for
    a fresh clone.
15. A short `learnings.md` entry records any PostgreSQL, storage, or upload-validation
    behaviour that was surprising or important for later phases.
16. The phase is committed as one bounded infrastructure milestone before Phase 7
    begins.

## Definition of done

The phase is done when all acceptance criteria pass and the repository can be run
without SQLite or public local placement media as hidden assumptions.

The next phase must not begin merely because the code "looks production-ready".
Point to the passing migration, check, storage, validation, security, and concurrency
tests that prove it.
