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
- `specs/phase-2-curriculum.md` — tracks, levels, placement review (CURRENT)
- Later phases (scheduling, routing, assessment, payouts) get their own
  spec file when we get there — don't write them in advance.

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
