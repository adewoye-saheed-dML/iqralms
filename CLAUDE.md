# Quran Academy Platform

Django REST backend for a Quran/Arabic teaching academy. Solves one problem:
lead teacher's time is the bottleneck; students span timezones and levels;
sub-teachers need routing + quality control without manual triage.

Full product context: see `docs/quran-academy-mvp-spec.md`. This file is a router
only — don't duplicate detailed phase content here, point to the active spec.

## Current phase

Check `specs/` for the active phase file. Work one phase at a time. Do not start
Phase N+1 until Phase N's acceptance criteria are met and committed.

- `specs/phase-1-accounts.md` — DONE
- `specs/phase-2-curriculum.md` — DONE
- `specs/phase-3-scheduling.md` — DONE
- `specs/phase-3.5-debt-cleanup.md` — DONE
- `specs/phase-4-routing.md` — DONE
- `specs/phase-5-pricing-waitlist.md` — DONE
- `specs/phase-6-production-hardening.md` — DONE
- `specs/phase-7-assessment-progress.md` — ACTIVE
- Later product phases get their own spec when they are reached. Do not write
  them in advance.

## Product-state rules carried forward

### Scheduling

`Booking.clean()` remains the source of booking eligibility. Routing must not
duplicate its business rules.

`TeacherBookingLock` is still required on PostgreSQL. New booking writers must
use the normal `Booking.save()` path and must not bypass the lock with
`bulk_create()`.

### Pricing

`pricing.PricingAgreement.active_for(student, level)` is the canonical lookup
for the active family rate. Do not reconstruct the active filter independently.

Pricing does not charge anyone and is independent of teacher payout.

### Waitlist

A `TeacherWaitlist` is created only by a refused preferred-teacher request.
Promotion is lead-controlled and must go through normal booking validation.

`TeacherWaitlist.notified` is not an automatic notification state yet.

### Placement audio

Placement recordings are private. Use the existing audio-access mechanism for
short-lived access; never restore `MEDIA_URL` or public media serving.

Upload limits and content validation remain in the curriculum validation layer.
Uploaded files are untrusted input.

## Phase 7 assessment boundary

Assessment is the quality-control layer between teaching and future routing
decisions.

Phase 7 owns:

- track-specific rubric configuration;
- per-completed-booking assessment;
- 1–5 criterion scoring;
- teacher summaries;
- optional lead-review flags;
- separate lead review annotations;
- lead-only teacher quality reporting;
- student/parent progress views;
- explicit historical progress snapshots.

Phase 7 does **not** change routing. In particular:

- do not rank teachers using assessment scores;
- do not make `routing.py` consume rubric averages;
- do not automatically change `recommended_level`;
- do not automatically suspend, promote, or demote teachers.

Assessment data is evidence for a future product decision, not that decision.

## Assessment invariants

Every assessment must preserve these rules:

- one assessment per booking;
- only the assigned booking teacher may assess;
- only completed bookings may be assessed;
- students and parents cannot assess;
- every active rubric criterion is scored exactly once;
- score values are 1 through 5;
- historical criterion names/order used by an assessment remain readable after
  live rubric edits;
- teacher scores are immutable after submission in this phase;
- lead review annotations are separate from teacher scores;
- lead review never rewrites historical teacher data.

Do not weaken these rules for convenience.

## Historical-data rule

Rubric configuration is live data; assessments are historical records.

Changing a rubric or criterion affects future assessments only. Every assessment
must preserve enough snapshot information to explain what the teacher was scoring
at submission time.

Do not build general-purpose rubric version graphs in this phase unless snapshots
prove insufficient. Stop and ask rather than silently adding another versioning
model.

## Reporting rules

Use one score definition everywhere:

`sum(all criterion scores) / number of criterion scores`

Missing assessments are missing data, never zero.

Per-criterion averages use only scores for that criterion.

Do not create teacher leaderboards or automated rankings in this phase.

Progress endpoints must enforce family scoping server-side. Serializer omission
alone is not a permission boundary.

## Stack & conventions

- Django + Django REST Framework, dj-rest-auth for token auth, drf-spectacular
  for API docs.
- PostgreSQL is canonical. No SQLite fallback.
- Placement object storage is private and accessed through short-lived signed
  URLs.
- Models live in `<app_name>/models.py`.
- Phase 7 introduces the `assessment/` Django app.
- One Django app per module (`accounts/`, `curriculum/`, `scheduling/`,
  `assessment/`, `pricing/`).
- Store all datetimes in UTC. Convert at serializer/view layer using the
  requesting user's stored `timezone`. Never store local time.
- Every model gets a factory in `tests/factories.py` before tests are written
  against it.
- Keep domain invariants in model/service code and permissions at the API layer.
- Educational-performance data is sensitive. Scope every queryset to the
  authenticated user's permitted teacher/student/family context.
- Never commit secrets, credentials, or private infrastructure identifiers.

## Testing requirements

For every Phase 7 model:

- test creation;
- test its key invariants;
- test historical-data behaviour where applicable.

For every Phase 7 endpoint:

- test the happy path;
- test at least one authorization failure;
- test an important domain failure.

Assessment tests must include:

- completed versus non-completed bookings;
- wrong-teacher attempts;
- duplicate assessment prevention;
- missing/duplicate criterion scores;
- score range validation;
- rubric edits after an assessment exists;
- flag/review lifecycle;
- teacher report aggregation;
- student/parent progress scoping;
- snapshot immutability and duplicate-period protection.

Run the full PostgreSQL suite before considering the phase done.

## Definition of done

- migrations run clean with `python manage.py migrate`;
- `python manage.py check` passes;
- `python manage.py makemigrations --check` passes;
- full Phase 1–7 suite passes against PostgreSQL;
- API schema generation/checks pass;
- every Phase 7 acceptance criterion has a passing test;
- no Phase 7 implementation changes routing behaviour;
- a concise `learnings.md` note records important assessment decisions;
- the phase is committed before Phase 8 begins.

"Looks right" is not done. Point to tests that prove the behaviour.

## When to stop and ask instead of guessing

Stop and ask when:

- two reasonable rubric/versioning models would both work;
- a change alters a Phase 1–6 model or invariant;
- a request could expose private assessment data;
- someone proposes letting a parent/student edit an assessment;
- someone proposes using assessment scores to change routing in this phase;
- a rule about retention, deletion, or historical-assessment correction is
  ambiguous.

Routine serializers, factories, admin wiring, and straightforward CRUD may be
implemented without another decision.

## Session hygiene

- One phase, or one clearly-scoped task inside a phase, per chat session.
- Before ending a session: tests passing, migrations committed, and a concise
  `learnings.md` note added for anything surprising.
- Never carry unfinished Phase 7 decisions into a later phase silently.

## Reference files

- `docs/quran-academy-mvp-spec.md` — overall product intent
- `specs/` — active and completed phase specifications
- `README.md` — environment/setup/pre-deploy workflow
- `.env.example` — environment variable contract
- `docker-compose.yml` — local PostgreSQL and private object storage
- `tech-debt.md` — deliberate shortcuts and later revisit points
- `learnings.md` — decisions and edge cases discovered during implementation
