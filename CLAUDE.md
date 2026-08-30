# Quran Academy Platform

Django REST backend for a Quran/Arabic teaching academy. The overall product intent is in `docs/quran-academy-mvp-spec.md`. Detailed requirements for each development stage live in `specs/`.

## Phase status

- Phase 1 Accounts — DONE
- Phase 2 Curriculum & placement — DONE
- Phase 3 Scheduling — DONE
- Phase 3.5 Booking debt cleanup — DONE
- Phase 4 Routing — DONE
- Phase 5 Pricing & preferred-teacher waitlist — DONE
- Phase 6 Production hardening — DONE
- Phase 7 Assessment & progress — IMPLEMENTED; manual acceptance pending
- Phase 8 Payouts & statements — IMPLEMENTED; manual acceptance pending

A phase is not complete merely because code exists. Do not start the next phase until the current phase's acceptance criteria are verified and the completed work is committed.

## Token-efficient development method

This repository is intentionally developed in small, inspectable increments. AI assistance must stay scoped to the smallest useful unit.

### Before changing code

1. Read only the active phase specification.
2. Choose one acceptance criterion or one clearly bounded task.
3. Check `git status`.
4. Inspect only the files needed for that task.
5. Prefer the current diff and existing repository patterns over rereading finished code.
6. Do not inspect the whole repository unless the task genuinely crosses module boundaries.

### During implementation

- Make one focused change at a time.
- Do not refactor unrelated code.
- Do not add speculative future fields, endpoints, abstractions, or infrastructure.
- Reuse existing repository conventions.
- Preserve behaviour outside the current task.
- Keep product decisions out of implementation guesses.
- When a requirement is ambiguous, stop and ask instead of inventing behaviour.

### One task per AI session

A normal coding request should look like:

> Implement Phase 8 task 8.1 only. Read the Phase 8 spec and inspect only the files required for that task. Do not modify unrelated apps. Do not write broad tests. Return changed files, why they changed, and the manual commands I should run.

Do not ask AI to implement an entire phase in one response.

## Manual testing is the default feedback loop

Routine testing is performed locally by the developer to reduce context and iteration cost.

After a focused change, normally run:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py test <targeted_app_or_test>
```

Then manually exercise the changed API through `/api/docs/` or the normal client.

The full regression suite should be run at the end of a phase or after a shared invariant changes.

AI should not repeatedly rerun broad suites when the developer can run them locally.

### When reporting a failure to AI

Send only:

- exact error or traceback;
- endpoint, command, or action;
- expected result;
- actual result;
- relevant file section or `git diff`.

Do not paste the entire repository or entire files unless the smaller context cannot explain the problem.

Good example:

```text
Endpoint: POST /api/payouts/generate/

Expected: one payout record for the eligible completed sessions.

Actual: IntegrityError on unique constraint.

Error:
<exact traceback>

Relevant diff:
git diff -- payouts/
```

## Prefer Git diffs as AI context

Use:

```bash
git status
git diff --stat
git diff -- <changed-file>
```

For previous work:

```bash
git show --stat <commit>
git show <commit> -- <file>
```

Small commits are checkpoints. Commit completed tasks frequently so every future session starts from a stable state.

## Source-of-truth rules

- `CLAUDE.md` — working rules and high-level phase status.
- `specs/` — detailed requirements for each phase.
- `docs/quran-academy-mvp-spec.md` — overall product intent.
- `learnings.md` — decisions, discoveries, and surprising edge cases.
- `tech-debt.md` — deliberate shortcuts and deferred work.
- `README.md` — environment and deployment workflow.

Do not create competing requirement documents.

## Product invariants carried forward

### Scheduling

`Booking.clean()` is the source of booking eligibility.

New booking writers must use the normal `Booking.save()` path and must not bypass validation with `bulk_create()`.

`TeacherBookingLock` remains part of the PostgreSQL booking-creation race protection. Do not weaken or remove it without a phase-specific decision and proof.

### Pricing

`pricing.PricingAgreement.active_for(student, level)` is the canonical lookup for the active family pricing agreement.

Family pricing is separate from teacher payout.

A discount or premium negotiated with a family must not silently change the teacher's payout rate unless the active payout rules explicitly say so.

### Waitlist

A `TeacherWaitlist` exists only for a refused preferred-teacher request.

Promotion is lead-controlled and must go through normal booking validation.

### Placement audio

Placement recordings remain private.

Never restore `MEDIA_URL` or public media serving.

Uploaded files are untrusted input and must use the existing validation and private-storage mechanism.

### Assessment

Assessment is historical quality-control evidence.

Preserve these invariants:

- one assessment per booking;
- only the assigned approved teacher may assess;
- only completed bookings may be assessed;
- every active criterion is scored exactly once;
- scores are 1–5;
- historical rubric snapshots remain readable after live rubric edits;
- teacher scores are immutable after submission;
- lead review is a separate annotation;
- family progress is server-side scoped;
- missing assessments are missing data, never zero.

Do not use assessment scores to change routing, teacher status, placement, or payout calculations unless a later phase explicitly defines that behaviour.

## Phase 8 boundary — Payouts & Statements

Phase 8 turns completed teaching activity into internal payout records and teacher statements.

It owns:

- teacher payout rates;
- payout eligibility from completed sessions;
- payout calculation;
- immutable payout records;
- statement periods;
- lead-only payout generation/management;
- teacher access to their own payout statements/history.

It does not own:

- family payment collection;
- payment gateways;
- bank transfers;
- invoices;
- tax accounting;
- currency conversion;
- automated payout execution;
- assessment-based pay changes;
- routing changes;
- pricing changes;
- notifications;
- automatic background statement jobs.

The core business separation is:

```text
Family pricing
     ≠
Teacher payout
```

The amount a family paid or negotiated must not become the teacher's payout unless the explicit payout rule says so.

## Payout security and historical-data rules

- A teacher may see only their own payout records and statements.
- A lead may manage payout records for the academy.
- Students and parents cannot access teacher payout data.
- Sub-teachers cannot access another teacher's payout data.
- Payout records represent historical financial decisions and must be treated as immutable once finalized.
- Changing a teacher's current payout rate affects future payout generation only.
- A previously finalized payout must not silently recalculate because a later rate changes.
- Cancelled and `no_show` bookings do not earn a teaching payout unless a later written rule explicitly changes this.
- Assessment scores do not alter payout amounts in Phase 8.
- Pricing agreements do not alter payout amounts in Phase 8.
- Never expose private financial data through broad or unscoped querysets.

## Stack & conventions

- Django + Django REST Framework.
- `dj-rest-auth` for token authentication.
- `drf-spectacular` for API documentation.
- PostgreSQL only; no SQLite fallback.
- One Django app per coherent module.
- Do not create a new app merely for one helper file.
- Models live in `<app_name>/models.py`.
- New models get factories before automated tests use them.
- Domain invariants belong in model/service code.
- API permissions belong at the API layer.
- Store datetimes in UTC.
- Convert datetime presentation at the serializer/view boundary using the user's stored timezone when needed.
- Reuse canonical domain helpers rather than duplicating business rules.

## Testing policy

Manual testing is the primary feedback loop during feature development.

Automated tests are still required for durable, high-risk financial invariants. Do not generate large suites for routine CRUD.

For Phase 8, high-value automated coverage should focus on:

- only completed eligible bookings contribute to payout;
- cancelled/no-show bookings do not contribute;
- payout rate is correctly applied;
- duplicate payout generation is prevented;
- payout records are immutable after finalization;
- later rate changes do not rewrite historical payouts;
- teacher scoping is enforced;
- lead-only management is enforced;
- family pricing does not silently change teacher payout;
- assessment data does not silently change payout;
- date/period boundaries are correct;
- aggregation is correct for multiple bookings.

Manual testing should cover the complete lead and teacher journeys before the phase is marked done.

## Definition of done

A phase is done when:

- its acceptance criteria are implemented;
- `python manage.py check` passes;
- `python manage.py makemigrations --check` passes;
- targeted automated tests for changed invariants pass;
- the developer manually verifies the complete user journey;
- previous-phase behaviour remains intact;
- important decisions are recorded in `learnings.md`;
- intentional shortcuts are recorded in `tech-debt.md`;
- the phase is committed before the next phase begins.

## Stop and ask instead of guessing

Stop when:

- a product choice is ambiguous;
- a previous-phase invariant must change;
- payout eligibility is unclear;
- rate changes and historical payouts could be interpreted in more than one reasonable way;
- someone proposes using family payment data as teacher payout without an explicit rule;
- someone proposes allowing a teacher to edit a finalized financial record;
- a proposal widens access to financial, student, parent, teacher, or assessment data;
- a change would alter the meaning of an existing historical record;
- automated scheduling, payment execution, or accounting infrastructure would be needed without an explicit phase decision.

Routine CRUD, serializers, factories, admin wiring, and explicit payout-calculation plumbing may proceed when the active spec is unambiguous.

## Session hygiene

- One phase, or one clearly bounded task, per chat session.
- Keep work small enough to manually verify.
- Commit completed tasks frequently.
- End each session at a clear checkpoint.
- Never carry unresolved product decisions into a later phase.
- Prefer the smallest next task that can be implemented and manually verified.
