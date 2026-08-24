# Phase 3.5 — Debt Cleanup (Booking Integrity)

## Goal

Close the tech-debt items that Phase 4 would otherwise build on top of and
make worse. Not a general debt-clearing pass — scoped tightly to booking
correctness, since that's what routing depends on.

## Why this phase exists, and why it's narrow

Phase 4 turns manual, occasional booking into automatic, higher-frequency
assignment. Two existing gaps get worse under that load, not just carried
forward unchanged:

- The overlap check can be raced — two simultaneous requests for the same
  slot can both pass and both commit. Routing will create exactly this
  kind of concurrent pressure on popular slots.
- Nothing stops a booking in the past. Not dangerous today with manual
  booking, but the routing algorithm will be generating time windows
  programmatically in Phase 4, and a bug there producing a past timestamp
  should fail loudly, not silently succeed.

Everything else currently in tech-debt.md is left alone on purpose — see
"Explicitly out of scope" below. Fixing it here would turn a tight
prerequisite phase into a second general cleanup sprint, which isn't what
was asked for.

## What gets fixed

**1. Booking overlap race condition**
- Add `select_for_update()` on the teacher's relevant `Availability` or a
  dedicated per-teacher lock row during `Booking` creation, inside a
  transaction, so two concurrent requests for the same teacher serialize
  instead of both reading a clean overlap check and both committing.
- This is a stopgap correct for SQLite/current stack — the tech-debt entry
  is right that the *real* fix is a Postgres exclusion constraint, and that
  entry stays in tech-debt.md unchanged. This phase makes the current
  behavior correct under concurrency, not perfect under scale.

**2. Past-booking rejection**
- `Booking.clean()` rejects a new booking whose `start_time_utc` is in the
  past, with a small grace window (a few seconds) so a request submitted
  at the boundary isn't spuriously rejected.
- Creation-time only, exactly as the tech-debt entry specifies — existing
  bookings must remain saveable after their start time passes, or every
  completed session becomes unsaveable.

**3. Tech-debt.md formatting**
- The `SECRET_KEY`/`DEBUG` entry is missing its `## [date] — title` header
  — restore it so the entry isn't orphaned under the previous one.

## Acceptance criteria

1. A concurrency test — two near-simultaneous requests for the same
   teacher's only open slot — asserts exactly one succeeds and the other
   gets a clean rejection, not a database integrity error or a silent
   double-booking.
2. A test asserts a booking with `start_time_utc` in the past is rejected.
3. A test asserts a booking at "now plus a few seconds" (within the grace
   window) is accepted — proving the grace period actually works, not just
   that the rule exists.
4. A test asserts an existing, already-`completed` booking with a past
   start time can still be saved (e.g. a status change) without tripping
   the new rule — this is what "creation-time only" must actually mean in
   code, not just in the spec's prose.
5. `tech-debt.md` renders correctly — every entry has a header, checked by
   eye, not a test.
6. `python manage.py check` and the full test suite pass, including
   Phases 1–3's existing tests (this phase touches shared model code —
   prove nothing regressed).

## Explicitly out of scope — deferred on purpose, not forgotten

- **Private storage + signed URLs for placement audio**, and **upload
  validation**. Real risks, but pre-*launch* blockers, not pre-*Phase 4*
  blockers — Phase 4 never touches `PlacementResult` or file uploads.
  Handle these in their own session before any real student signs up.
- **SQLite → Postgres migration.** The concurrency fix above is deliberately
  scoped to work on the current stack rather than forcing this move now.
- **`full_clean()` in `save()` performance/bulk-write concerns.** Unrelated
  to booking integrity, no new pressure on it from this phase.

If Phase 4 building surfaces something these fixes should have covered and
didn't, stop and say so rather than quietly patching it inside Phase 4's
scope — it likely belongs back here or in its own entry.
