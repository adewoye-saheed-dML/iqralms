# Phase 4 — Routing Engine

## Goal

Given a student wanting a session at a given level and time, the system
decides who teaches it — cohort, you, or a matched sub-teacher — instead of
a human picking a teacher every time. This is the actual answer to the
original problem: your time is the bottleneck, and this phase is what stops
that being true.

## Why this phase now

Phase 3 gave us `Booking`, `Availability`, and `TeacherProfile.specialties`
with nothing reading the last one yet. This phase is what finally uses all
three together, plus a new `Cohort` model for group classes.

## Prerequisite

`specs/phase-3.5-debt-cleanup.md` must be complete first — it closes the
booking concurrency race condition that auto-assignment would otherwise
make worse. Do not start this phase until 3.5's acceptance criteria pass.

## One tech-debt item this phase must close, not just reference

- **Specialties enforcement** (tech-debt, Phase 3): `Booking.clean()` must
  now reject a level whose track isn't in the teacher's `specialties`,
  whether the booking came through direct booking or routing.

## Data model

**Cohort**
- `teacher`: FK to User
- `level`: FK to `curriculum.Level` (must have `group_eligible=True` —
  enforce in `clean()`)
- `max_students`: positive integer, default 6
- `schedule_start_utc`: datetime
- `students`: M2M to User, capped at `max_students` — enforce in `clean()`
  or a custom `add_student()` method, not just at the serializer layer

**Booking** (extends Phase 3's model)
- `cohort`: FK to Cohort, nullable — set when a booking is a cohort seat
  rather than a 1:1 session
- `routed_reason`: choice field — `lead_available`, `lead_full_routed`,
  `student_choice`, `cohort_assigned`. This is the field Phase 3 explicitly
  said not to add yet; add it now.

**TeacherProfile** (extends Phase 1)
- No new fields. `max_weekly_hours` (already exists) becomes enforced for
  the first time this phase — see rules below.

## The routing algorithm

Given `(student, level, requested_time_window)`:

1. **Cohort check first.** If `level.group_eligible` and an open `Cohort`
   exists for that level with a `schedule_start_utc` reasonably close to
   the requested window and available seats, assign the student to it.
   `routed_reason = cohort_assigned`.
2. **Lead capacity check.** If you (the single `is_lead=True` teacher) have
   an `Availability` window covering the requested time AND your total
   `scheduled` booking minutes for the current week are under
   `max_weekly_hours`, assign to you. `routed_reason = lead_available`.
3. **Sub-teacher match.** Filter approved sub-teachers whose
   `specialties` include the level's track, who have availability covering
   the window, and who are under their own `max_weekly_hours`. Pick one —
   simplest correct rule for this phase: whoever has the most remaining
   weekly capacity (spreads load evenly). Don't build anything fancier
   (rubric-average-based ranking, etc.) yet — that's a later refinement,
   log it in tech-debt.md if you're tempted. `routed_reason =
   lead_full_routed`.
4. **Nothing available.** Return a clear "no capacity" response rather than
   forcing an assignment. Don't book someone outside their availability or
   over their capacity just to avoid returning an error — that defeats the
   entire point of this phase.

Direct booking (Phase 3's endpoint, where a human picks the teacher) still
exists and still works — `routed_reason = student_choice` in that case.
The specialties + capacity rules from Phase 4 apply to it too, per the
tech-debt closure above.

## Rules to enforce

- `max_weekly_hours` is now a hard cap, checked at booking creation time,
  for both you and sub-teachers. A booking that would push a teacher over
  their weekly cap is rejected, not just discouraged.
- "Current week" for the capacity calculation: define it clearly (e.g.
  Monday–Sunday in UTC) and use the same definition everywhere — don't let
  the routing check and any future payout calculation use different week
  boundaries.
- A `Cohort` can't be assigned a level where `group_eligible=False` —
  enforce this even though the admin UI should already prevent it; direct
  ORM/fixture creation shouldn't be able to violate it.
- If the routing algorithm's steps 1–3 all fail, do not fall through to
  "just pick anyone anyway." A clear failure is correct behavior here, not
  a bug to route around.

## API surface

- `POST /api/scheduling/route/` — takes `student`, `level`,
  `requested_time_window`; runs the algorithm; either creates the
  resulting `Booking` (or adds the student to a `Cohort`) or returns a
  structured "no capacity" response the frontend can show honestly.
- `POST /api/scheduling/cohorts/` — admin/lead creates a cohort (teacher,
  level, schedule, max_students).
- `GET /api/scheduling/cohorts/open/?level_id=` — lists cohorts with
  available seats for a level, used internally by the routing check and
  usable directly if you want a "browse open cohorts" view later.

## Acceptance criteria

1. Migrations apply cleanly on top of Phases 1–3, fresh DB.
2. A test creates an open cohort and asserts a matching routing request
   gets `routed_reason=cohort_assigned` and the student added to
   `cohort.students`.
3. A test asserts a request within your availability and under your
   capacity routes to you with `routed_reason=lead_available`.
4. A test fills your weekly capacity, then asserts the next matching
   request routes to a sub-teacher instead, with
   `routed_reason=lead_full_routed`.
5. A test asserts a sub-teacher over their own `max_weekly_hours` is never
   selected, even if they're otherwise the best specialty match.
6. A test asserts a request with no cohort, no lead capacity, and no
   matching sub-teacher returns a clear failure, not a forced booking.
7. A test asserts a direct booking (Phase 3's endpoint) against a level
   outside the teacher's specialties is now rejected — this is the
   tech-debt closure, prove it actually closed.
8. A regression test confirms Phase 3.5's concurrency fix still holds
   under routing's auto-assignment path specifically, not just direct
   booking — the fix landed in 3.5, this just proves it covers the new
   entry point too.
9. `python manage.py check` and the full test suite pass.

## Explicitly out of scope for this phase

Pricing exceptions, preferred-teacher waitlists, rubric-based sub-teacher
ranking (mentioned above — resist it), payouts. Do not let "whoever has
most capacity" in step 3 quietly turn into a scoring system with weights —
that's a real design decision for a later phase, not something to
back into here.
