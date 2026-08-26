# Quran Academy Platform

Django REST backend for a Quran/Arabic teaching academy. Solves one problem:
lead teacher's time is the bottleneck; students span timezones and levels;
sub-teachers need routing + quality control without manual triage.

Full product context: see `docs/mvp-spec.md` (all phases, data model reasoning).
This file is a router only — don't duplicate spec content here, point to it.

## Current phase

Check `specs/` for the active phase file. Work one phase at a time. Do not
start Phase N+1 until Phase N's acceptance criteria in its spec file are met
and committed.

- `specs/phase-1-accounts.md` — users, roles, parent-child linking (DONE)
- `specs/phase-2-curriculum.md` — tracks, levels, placement review (DONE)
- `specs/phase-3-scheduling.md` — availability, direct booking, video (DONE)
- `specs/phase-3.5-debt-cleanup.md` — booking concurrency + past-booking fix (DONE)
- `specs/phase-4-routing.md` — cohorts, capacity-based auto-assignment (DONE)
- `specs/phase-5-pricing-waitlist.md` — pricing exceptions, preferred-teacher waitlist (DONE)
- `specs/phase-6-production-hardening.md` — PostgreSQL, private media, upload validation, production configuration (DONE)
- Later product phases get their own spec file when we get there — don't
  write them in advance.

## Notes carried out of Phase 5

Two apps now exist that a payments phase will read, and neither charges
anyone: `pricing.PricingAgreement` records what a rate *should* be, and
`Booking` still has no price field. Read a live rate through
`PricingAgreement.active_for(student, level)` — don't assemble the
`active=True` filter again, for the same reason capacity has one
`weekly_committed_minutes`.

Three rules that anything touching the waitlist has to respect:

- Entries are created **only** by a refused preferred-teacher request. There
  is no create endpoint, and promotion is the only way one closes.
- `routing.WAITLISTABLE_CODES` is where routing *interprets* `Booking.clean()`
  rather than relaying it, so a new refusal code added to `clean()` silently
  defaults to "fail outright, never waitlist". Decide that on purpose and say
  which in the commit — see `learnings.md`, 2026-08-25.
- Promotion is one-way: cancelling a promoted session leaves the entry
  closed, and re-asking creates a fresh row that loses its place in the
  queue. Deliberate (`tech-debt.md`, 2026-08-25), with tests pinning it.

`TeacherWaitlist.notified` is written by nothing at all — automatic
notification is a phase of its own. Don't read it as "nobody has been told yet".

## Phase 6 hardening boundary

Phase 6 was the pre-launch infrastructure boundary. All four items below are now
closed, and each has a test that fails if it comes back:

- SQLite as the canonical application database;
- public/local placement-media serving;
- placement uploads without size, format, and content validation;
- development-only security defaults being usable as production configuration.

Phase 6 was deliberately not a product-feature phase. Do not treat the notes
below as an invitation to redesign cohorts, routing, pricing, waitlists,
assessment, or payments.

## Notes carried out of Phase 6

**Running anything now needs two things.** `DATABASE_URL` (PostgreSQL, no
fallback — a missing or non-PostgreSQL URL raises at settings import) and a
private storage backend. `README.md` has the fresh-clone path;
`.env.example` documents every variable. `docker compose up -d --wait` gives you
both locally.

**There is no `MEDIA_URL` and no media route, and that is load bearing.**
`PrivateLocalStorage.url()` *raises* rather than returning a path, so a
serializer or template that reaches for a public URL fails at the line that made
the mistake. To let someone hear a recitation sample, use
`curriculum.audio.placement_audio_access()` — the one function that mints
short-lived URLs — or point them at
`/api/curriculum/placements/{id}/audio-url/`. Never re-add `static(MEDIA_URL...)`.

Who may hear a sample was the product owner's call (2026-08-26): the **lead
teacher and the owning student**, nobody else. A minor's linked parent is
refused, with a test asserting the 403. Widening that is a product decision, not
a convenience — ask.

**`TeacherBookingLock` survived the migration, and there is a test that says
why.** PostgreSQL's default READ COMMITTED does not make a check-then-insert
safe, so `test_concurrency.LockIsStillRequiredOnPostgresTests` reproduces the
double booking with the lock patched out. Acquisition is now a real
`SELECT ... FOR UPDATE`; `revision` is diagnostic only. Any *new* code that
reads neighbouring rows and then writes needs the same lock — a `bulk_create`
still bypasses it and `clean()` alike.

**`DJANGO_PRODUCTION=1`, not `not DEBUG`, gates the HTTPS settings.** Tying them
to `DEBUG` would switch TLS redirects on for the test suite and every local
management command. Before deploying: `python manage.py check --deploy
--fail-level WARNING`, which currently passes with zero warnings — keep it that
way, including for warnings that look cosmetic (an unnamed drf-spectacular enum
is a failing check, not a nit).

**Upload limits live in `curriculum/validators.py`, not in settings.** 15 MiB and
six formats, with the file's own leading bytes deciding — `Content-Type` is
evidence, never proof. `config/settings.py` imports the byte cap from there, so
that module must stay import-light: no models, no settings, or loading settings
breaks.

## Stack & conventions

- Django + Django REST Framework, dj-rest-auth for token auth, drf-spectacular
  for API docs.
- PostgreSQL is the canonical database from Phase 6 onward. Do not add SQLite
  fallbacks for convenience.
- Object storage for placement audio is private. Access is granted through
  short-lived signed URLs or an equivalent private mechanism.
- Models live in `<app_name>/models.py`. One Django app per module
  (`accounts/`, `curriculum/`, `scheduling/`, `assessment/`, `payouts/`).
- Store all datetimes in UTC. Convert at the serializer/view layer using the
  requesting user's stored `timezone` field. Never store local time.
- Every model gets a factory in `tests/factories.py` for that app (use
  `factory_boy`) before writing tests against it.
- Prefer model validation as the single source of business eligibility where
  routing already relies on `Booking.clean()`. Do not duplicate domain rules
  in serializers or routing without a concrete reason.
- Keep security-sensitive configuration environment-driven. Never commit real
  secrets, credentials, or production infrastructure identifiers.
- User-uploaded files are untrusted input. Validate size, declared type, and
  actual content before persistence.

## Definition of done (for every unit of work)

- Migrations run clean (`python manage.py migrate` with no errors).
- Model fields match the spec file exactly — no silently-added fields.
- At least one test per model (creation) and per endpoint (happy path +
  one failure case) actually runs and passes. Not described — run.
- `python manage.py check` passes.
- Production-facing changes also pass the relevant deployment checks and
  security tests.
- The full suite is run against PostgreSQL once Phase 6 begins.
- Committed with a message describing what and why, not just what.

"Looks right" is not done. If you can't point to a passing test that proves
the behavior, it isn't finished.

## When to stop and ask instead of guessing

Stop and ask me directly (don't pick silently) when:
- Two reasonable ways to model the same thing exist (e.g. should pricing
  live on Booking or its own table) — this happened before, always ask.
- A change would touch a model from an earlier, already-committed phase.
- Anything about payments, auth security, or data deletion is ambiguous.
- A production-hardening choice changes who can access stored student data.
- A PostgreSQL change would alter an existing business invariant rather than
  merely preserving it on the new database.

Routine model fields, serializer boilerplate, obvious CRUD endpoints — just
build it, no need to check in.

## Session hygiene

- One phase (or one clearly-scoped task within a phase) per chat session.
  Don't carry a single long session across multiple unrelated pieces of work.
- Before ending a session: tests passing, migrations committed, and a short
  note added to `learnings.md` if anything surprising came up.
- The full suite runs against PostgreSQL and takes ~4 minutes, most of it the
  concurrency tests. Run it anyway — they are what prove two people cannot book
  one slot. Use `--parallel` if it becomes painful; do not convert those tests to
  `TestCase`, which would silently stop testing the race.

## Reference files

- `docs/mvp-spec.md` — full product spec, all phases
- `specs/` — phase-specific active and completed specifications
- `README.md` — fresh-clone setup, environment variables, pre-deploy checks
- `.env.example` — every variable the application reads, with its default
- `docker-compose.yml` — local PostgreSQL and a private S3-compatible bucket
- `tech-debt.md` — known shortcuts taken for MVP speed, revisit later
- `learnings.md` — edge cases and gotchas discovered while building, so we don't
  relearn them next session
