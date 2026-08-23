# Phase 3 — Scheduling & Video

## Goal

A student can book a session with a specific, available, approved teacher
at a specific time, and both sides get a working video link. No automatic
routing, no cohorts, no capacity-based teacher selection — a human (parent
or student) picks the teacher and slot directly. That intelligence is
Phase 4, on top of this.

## Why this phase before routing

The routing engine (Phase 4) needs a working `Booking` model and a way to
check "is this teacher free at this time" before it can make decisions on
top of that. Building the decision layer before the thing it decides over
is backwards.

## This phase touches an earlier phase's model — flagging per CLAUDE.md

`TeacherProfile` (from Phase 1) needs a `specialties` field — a
many-to-many to `curriculum.Track` — so booking can know which tracks a
teacher is eligible to teach. This is a schema change to an already-shipped
model. Confirm this is fine before starting; if it's not, say so and we'll
find another way to represent it (e.g. a separate join table instead of
touching `TeacherProfile` directly).

## Data model

**Availability**
- `teacher`: FK to User (must have `is_teacher` and an `approved=True`
  TeacherProfile)
- `weekday`: 0–6
- `start_time_utc`, `end_time_utc`: time fields, stored UTC per CLAUDE.md
  convention — the teacher's local time is converted at the point of entry
  using their `User.timezone`, never stored local.
- A teacher can have multiple `Availability` rows (multiple windows per
  day, or different windows on different days).

**Booking**
- `student`: FK to User (role=`student`)
- `teacher`: FK to User (role=`lead` or `sub`)
- `level`: FK to `curriculum.Level`
- `start_time_utc`: datetime
- `duration_minutes`: positive integer, default 30
- `status`: `scheduled`, `completed`, `no_show`, `cancelled`
- `video_room_name`: string, generated automatically on creation (a UUID
  or similarly unguessable value — this becomes the Jitsi room identifier)
- Unique-ish constraint: no two `scheduled` bookings for the same teacher
  with overlapping `[start_time_utc, start_time_utc + duration_minutes)`
  ranges. Enforce in `clean()`, not just at the API layer, same pattern as
  Phase 1's role checks.

## Rules to enforce

- A booking can only be created against a teacher who has an
  `Availability` row covering that weekday and time window, and an
  `approved=True` TeacherProfile. Reject otherwise — don't silently book
  outside declared hours.
- Overlap check above applies per teacher, not per student — a student
  double-booking themselves across two different teachers isn't this
  phase's problem to solve (flag it in learnings.md if it comes up, don't
  silently add a constraint for it).
- `video_room_name` is generated once at creation and never changes, even
  if the booking is rescheduled (rescheduling isn't in scope this phase —
  cancel and recreate is the only supported path for now).
- Cancelling a booking sets `status=cancelled`; it does not delete the row.
  We need the history later for payouts and attendance, even this early.
- No specialty/eligibility enforcement between `Level`'s track and the
  teacher's `specialties` yet — track it as a known gap in tech-debt.md
  rather than building it now. This phase trusts whoever is booking to
  pick a sensible teacher; Phase 4's matching logic is where this actually
  gets enforced.

## Video integration

- Use Jitsi Meet's free tier (self-hosted `meet.jit.si` domain is fine for
  this phase — don't stand up self-hosted infrastructure yet, that's a
  later optimization once volume justifies it).
- No API call needed to "create" a room — the room exists the moment the
  first participant joins that room name. `video_room_name` just needs to
  be unique and hard to guess.
- Return the constructed join URL (e.g. `https://meet.jit.si/{video_room_name}`)
  from the booking API response — the frontend embeds it via the iframe
  API later; this phase just needs the URL to exist and be correct.

## API surface

- `GET /api/scheduling/availability/?teacher_id=` — public, lists a
  teacher's availability windows.
- `POST /api/scheduling/bookings/` — authenticated student or their linked
  parent creates a booking. Validates availability + no overlap, generates
  `video_room_name`.
- `GET /api/scheduling/bookings/mine/` — student sees their own bookings
  (past and upcoming).
- `GET /api/scheduling/bookings/teaching/` — teacher sees bookings where
  they're the assigned teacher.
- `POST /api/scheduling/bookings/{id}/cancel/` — either party cancels;
  sets status, doesn't delete.

## Acceptance criteria

1. Migrations apply cleanly on top of Phase 1 + 2, fresh DB.
2. A test creates an `Availability` and asserts a `Booking` inside that
   window succeeds; one outside it is rejected.
3. A test creates two overlapping booking attempts for the same teacher
   and asserts the second is rejected; a non-overlapping one for the same
   teacher succeeds.
4. A test asserts a booking against a teacher with `approved=False` is
   rejected.
5. A test asserts `video_room_name` is present and unique across two
   different bookings.
6. A test asserts cancelling sets `status=cancelled` and the row still
   exists (not deleted).
7. A test asserts a student cannot see another student's bookings via
   `bookings/mine/`.
8. `python manage.py check` and the full test suite pass.

## Explicitly out of scope for this phase

Cohorts, the routing engine, capacity limits (`max_weekly_hours` isn't
enforced yet — a teacher could theoretically be booked past their stated
capacity this phase; that's Phase 4's job), rubric assessment, payouts,
pricing exceptions, preferred-teacher waitlists. Do not add a
`routed_reason` field or any routing-related column to `Booking` in this
phase — Phase 4 adds what it needs when it needs it.
