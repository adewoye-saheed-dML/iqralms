# Phase 5 — Pricing Exceptions & Preferred-Teacher Waitlist

## Goal

Two things a parent might ask for that the routing engine doesn't handle:
paying a different rate than the standard one, and wanting a *specific*
teacher even when that teacher is full. Both get tracked explicitly rather
than handled by memory or a silent override — that was the design
principle from the original discussion, and this phase is where it
actually gets built.

## Why this phase now, and why it leans on Phase 4

Phase 4's `routing._candidate()` already knows how to test "could this
teacher take this session" by building an unsaved `Booking` and calling
`full_clean()`. Preferred-teacher requests are the same question asked
about exactly one teacher instead of a pool — so this phase must reuse
that function, not reimplement eligibility checking. Your own
`learnings.md` (2026-08-24, "Routing tests a candidate...") already says
this phase should extend `considered` rather than invent a second
reporting shape. Follow that.

## Data model

**PricingAgreement**
- `student`: FK to User (role=`student`)
- `level`: FK to `curriculum.Level` — pricing is per level, a student can
  have a different arrangement for different tracks/levels
- `standard_rate`: decimal — what it would normally cost, snapshot at
  creation time (don't derive it live from somewhere else; if the
  standard rate changes later, existing agreements shouldn't silently
  reprice)
- `agreed_rate`: decimal — what was actually negotiated
- `reason`: choice — `discount_hardship`, `premium_direct`,
  `sibling_discount`, `standard`
- `approved_by`: FK to User — must be the lead teacher, enforced the same
  way `PlacementResult.reviewed_by` is gated to lead-only
  (`IsLeadTeacher`, plus a `clean()` check for direct ORM writes)
- `notes`: text, private — what was actually agreed and why
- `active`: boolean, default True

**TeacherWaitlist**
- `student`: FK to User
- `requested_teacher`: FK to User (must be a teacher — `is_teacher`)
- `level`: FK to `curriculum.Level`
- `requested_at`: auto-now-add
- `priority`: integer, default 0 — manually set, not auto-computed (see
  the open question below)
- `notified`: boolean, default False
- `fulfilled_booking`: FK to `scheduling.Booking`, nullable — set when a
  waitlist entry is promoted into an actual booking, so the row keeps a
  permanent record instead of being deleted on fulfillment

## Rules to enforce

- **A student can have at most one *active* `PricingAgreement` per
  level.** Creating a new one for a student+level that already has an
  active agreement deactivates the old one (`active=False`) rather than
  deleting it — pricing history is worth keeping, unlike the
  keep-current-only call made for placement audio. This is a financial
  record; don't apply that phase's retention answer here without
  re-deciding it, and this phase's answer is: keep everything.
- **Only the lead can create or approve a `PricingAgreement`.** No
  exceptions, no sub-teacher path — this mirrors the placement-review
  gate exactly, both in the permission class and in `clean()` so a direct
  ORM write can't bypass it either.
- **A preferred-teacher request always produces a 1:1 booking, never a
  cohort seat.** A parent naming a specific teacher wants that teacher,
  not a seat in a class — even if that teacher happens to have an open
  cohort at the right time. If cohort-aware preferred-teacher requests
  turn out to be wanted later, that's a distinct feature to design on
  purpose, not something to fold in here. Log this scope decision in
  tech-debt.md so it doesn't read as an oversight later.
- **This phase does not touch payments.** `PricingAgreement` is a lookup
  table recording what rate *should* apply — nothing in this phase
  charges anyone anything, and `Booking` gets no new price field. A
  future payments phase reads `PricingAgreement`, this phase only
  produces the data for it to read.

## Open question — stop and ask, don't default

Should a `PricingAgreement` with `reason=premium_direct` automatically
bump a student's `TeacherWaitlist.priority`? This is a real business call
about whether paying more buys faster access to you specifically, and it
was flagged as exactly that kind of decision in the original product
discussion. Don't decide it silently either direction — ask, then
implement whichever answer comes back. If the answer is no, `priority`
stays a plain manually-set field with no automatic inputs this phase.

## Preferred-teacher routing flow

Extend the existing routing entry point (`route_session()` or its API
wrapper) to accept an optional `preferred_teacher` argument:

1. If `preferred_teacher` is given, skip the cohort → lead → sub-teacher
   sequence entirely. Build a single candidate against that teacher only,
   using the same `_candidate()` mechanism Phase 4 already has.
2. If the candidate is eligible (passes `full_clean()`), save it —
   `routed_reason=student_choice`, exactly as Phase 3 already defined.
3. If the candidate is *not* eligible because of capacity or availability
   specifically (not because of a hard block like an unapproved profile
   or a specialty mismatch — those should still fail outright, not
   waitlist), create a `TeacherWaitlist` entry instead of falling through
   to a sub-teacher. Nobody gets silently redirected to someone they
   didn't ask for.
4. The response in the waitlist case extends `NoCapacity.considered` with
   the waitlist entry's id, rather than inventing a new response shape —
   per your own learnings.md note on this exact point. The parent sees
   why the preferred teacher wasn't available right now *and* that
   they're on a list for that teacher specifically.

## Waitlist fulfillment (manual this phase, not automated)

- `GET /api/scheduling/waitlist/for-teacher/?teacher_id=` — lists open
  waitlist entries for a teacher, ordered by `priority` desc then
  `requested_at` asc.
- `POST /api/scheduling/waitlist/{id}/promote/` — lead manually converts
  an entry into a booking. This must go through the normal candidate
  check and `Booking.save()` path — same warning as Phase 4's routing:
  no `bulk_create`, no hand-built row that skips the teacher lock or
  `clean()`. On success, stamp `fulfilled_booking` and leave the entry in
  place as a record, don't delete it.
- Automatic notification/offering when a slot frees up is explicitly out
  of scope this phase — a lead manually checking the waitlist and
  promoting an entry is the whole mechanism for now. Automating "notify
  the next person in line the moment a slot opens" is real work
  (background jobs, notification delivery) that deserves its own phase.

## API surface

- `POST /api/pricing/agreements/` — lead creates a pricing exception.
- `GET /api/pricing/agreements/?student_id=` — lead views a student's
  pricing history (active and superseded).
- `GET /api/pricing/agreements/mine/` — student sees their own active
  agreement per level, not the full history or the `notes` field (that's
  the lead's private reasoning, not the family's business).
- Routing endpoint (Phase 4's) extended with optional `preferred_teacher`.
- `GET /api/scheduling/waitlist/mine/` — student sees their own waitlist
  entries and status.
- `GET /api/scheduling/waitlist/for-teacher/?teacher_id=` — as above.
- `POST /api/scheduling/waitlist/{id}/promote/` — as above.

## Acceptance criteria

1. Migrations apply cleanly on top of Phases 1–4, fresh DB.
2. A test asserts a sub-teacher cannot create a `PricingAgreement` (403
   or equivalent), mirroring the placement-review permission test.
3. A test creates a second `PricingAgreement` for the same student+level
   and asserts the first is deactivated, not deleted — both rows still
   exist, queryable.
4. A test sends a routing request with `preferred_teacher` set to a
   teacher who has capacity, and asserts it books directly with
   `routed_reason=student_choice`, no waitlist entry created.
5. A test sends a routing request with `preferred_teacher` set to a
   teacher who is at capacity, and asserts a `TeacherWaitlist` entry is
   created and the response includes it in `considered` — not a generic
   `NoCapacity` with no trace of the preference.
6. A test asserts a preferred-teacher request against a group-eligible
   level still produces a 1:1 booking attempt, never a cohort seat, even
   if that teacher has an open cohort at the right time.
7. A test promotes a waitlist entry and asserts it goes through
   `Booking.save()` properly — locked, validated — and
   `fulfilled_booking` is stamped, with the waitlist row still present
   afterward.
8. A test asserts promoting a waitlist entry for a teacher who is no
   longer eligible (capacity filled by something else since the request
   was made) fails cleanly rather than forcing the booking.
9. `python manage.py check` and the full test suite pass.

## Explicitly out of scope for this phase

Automated waitlist notifications, payments/charging, any UI for browsing
open cohorts near a preferred teacher, priority auto-computation beyond
whatever the open question above resolves to. Recurring cohorts remain a
separate, not-yet-scheduled piece of work — this phase doesn't touch
`Cohort` at all beyond the "no cohort seats from preferred-teacher
requests" rule above.
