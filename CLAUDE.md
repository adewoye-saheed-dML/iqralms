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
- Later phases (pricing/waitlist, assessment, payouts) get their own spec
  file when we get there — don't write them in advance. Next up is pricing
  exceptions + the preferred-teacher waitlist, which mvp-spec sections 3 and 4
  describe and Phase 4 deliberately left out.

## Notes carried out of Phase 4

Three rules landed in `Booking.clean()` that anything creating a booking must
now satisfy — they are creation-only, so editing a teacher's hours, specialties
or cap never freezes bookings they already hold:

- The teacher must specialise in the level's track. A teacher with **no**
  specialties recorded therefore teaches nothing and is unbookable until their
  tracks are set in the admin (tech-debt.md).
- `max_weekly_hours` is a hard cap. "The week" is `utils.week_bounds` (Monday
  00:00 UTC, Monday to Monday) and nothing else — payouts must read that and
  `models.weekly_committed_minutes`, not assemble their own sum.
- A cohort seat is an ordinary `Booking` with `cohort` set, and seats sharing a
  cohort deliberately do not clash. Any future exclusion constraint needs that
  same exemption.

Routing writes through `Booking.save()` (`scheduling/routing.py`), which is what
takes `TeacherBookingLock` — `scheduling/tests/test_concurrency.py` has a test
that fails specifically if a cohort seat is ever written with `bulk_create`.
Routing also tests candidates by building an unsaved `Booking` and calling
`full_clean()`, so it holds no copy of the eligibility rules; add rules to
`clean()` and routing picks them up.

## Known pre-launch blockers (see tech-debt.md for full detail)

- Placement audio is stored on local disk and publicly readable by URL in
  DEBUG mode. Must move to private object storage with signed URLs before
  any real student uploads anything.
- No upload validation (type, size, magic bytes) on placement audio.

Neither blocks Phase 3+ development, but neither should still be true when
real students start using this.

## Stack & conventions

- Django + Django REST Framework, dj-rest-auth for token auth, drf-spectacular
  for API docs.
- Models live in `<app_name>/models.py`. One Django app per module
  (`accounts/`, `curriculum/`, `scheduling/`, `assessment/`, `payouts/`).
- Store all datetimes in UTC. Convert at the serializer/view layer using the
  requesting user's stored `timezone` field. Never store local time.
- Every model gets a factory in `tests/factories.py` for that app (use
  `factory_boy`) before writing tests against it.

## Definition of done (for every unit of work)

- Migrations run clean (`python manage.py migrate` with no errors).
- Model fields match the spec file exactly — no silently-added fields.
- At least one test per model (creation) and per endpoint (happy path +
  one failure case) actually runs and passes. Not described — run.
- `python manage.py check` passes.
- Committed with a message describing what and why, not just what.

"Looks right" is not done. If you can't point to a passing test that proves
the behavior, it isn't finished.

## When to stop and ask instead of guessing

Stop and ask me directly (don't pick silently) when:
- Two reasonable ways to model the same thing exist (e.g. should pricing
  live on Booking or its own table) — this happened before, always ask.
- A change would touch a model from an earlier, already-committed phase.
- Anything about payments, auth security, or data deletion is ambiguous.

Routine model fields, serializer boilerplate, obvious CRUD endpoints — just
build it, no need to check in.

## Session hygiene

- One phase (or one clearly-scoped task within a phase) per chat session.
  Don't carry a single long session across multiple unrelated pieces of work.
- Before ending a session: tests passing, migrations committed, and a short
  note added to `learnings.md` if anything surprising came up.

## Reference files

- `docs/mvp-spec.md` — full product spec, all phases
- `tech-debt.md` — known shortcuts taken for MVP speed, revisit later
- `learnings.md` — edge cases and gotchas discovered while building, so we
  don't relearn them next session
