# Phase 2 — Curriculum & Placement

## Goal

Define what can be taught (tracks and levels within them) and get a new
student assigned a starting level via an async-reviewed placement sample.
Nothing about booking a session against that level yet — this phase ends
once a student has a `recommended_level`, not once they've had a class.

## Why this phase now

Scheduling (Phase 3) needs a `Level` to attach a `Booking` to, and the
routing engine (Phase 4) needs `Level.group_eligible` to decide cohort vs
1:1. Placement has to exist before either, or there's nothing to route
against.

## Data model

**Track**
- `name`: e.g. "Tajweed", "Hifz", "Arabic", "Noorani Qaida"
- `slug`: unique, for URLs/API filtering

**Level**
- `track`: FK to Track
- `order`: positive integer, defines sequence within the track (1 = first)
- `name`: e.g. "Beginner", "Intermediate Tajweed"
- `min_age`: nullable positive integer — informational only this phase, not
  enforced as a hard gate (a 10-year-old and a 40-year-old can both be
  "Beginner Arabic")
- `group_eligible`: boolean — whether this level is allowed to run as a
  cohort later. Set the data now even though cohorts don't exist until
  Phase 4.
- Unique together: (`track`, `order`) — no two levels in the same track
  share a position.

**PlacementResult**
- `student`: FK to User (must have role=`student`)
- `track`: FK to Track — a student can have a separate placement per track
  (e.g. placed into intermediate Tajweed but beginner Arabic)
- `audio_sample`: file field, nullable
- `skipped_as_beginner`: boolean, default False — set True when the student
  marks themselves complete beginner instead of submitting audio. Exactly
  one of `audio_sample` or `skipped_as_beginner=True` must be set; enforce
  this, don't allow both empty or both filled.
- `recommended_level`: FK to Level, nullable until reviewed
- `reviewed_by`: FK to User, nullable until reviewed
- `reviewed_at`: nullable datetime
- `status`: `pending`, `reviewed` — derive from whether `reviewed_by` is set
  rather than storing it redundantly, unless that makes the queryset
  annoying, in which case store it and say why in learnings.md

## Rules to enforce

- A student can only have one `PlacementResult` per `track` at a time. A
  second placement request for the same track should update the existing
  row (reset to pending, clear the review), not create a duplicate.
- `reviewed_by` must be a User with role `lead` or `sub`, and — this is the
  one open question, don't guess — **stop and ask me**: should any approved
  sub-teacher be allowed to review placements, or only the lead? The MVP
  spec said "you or a senior teacher," which is vague on purpose because I
  hadn't decided. Ask, don't default either way.
- `skipped_as_beginner=True` results in `recommended_level` auto-set to
  that track's `order=1` level, no human review needed. Still stamp
  `reviewed_by` as null and `status` as `reviewed` immediately — this is a
  system decision, not a teacher one, and the data should reflect that.
- Levels within a track must not have gaps or duplicate `order` values
  enforced at save time, not just by convention.

## API surface

- `GET /api/curriculum/tracks/` — public list, includes nested levels
  ordered correctly.
- `POST /api/curriculum/placements/` — authenticated student submits either
  an audio file or `skipped_as_beginner=true` for a given track.
- `GET /api/curriculum/placements/pending/` — authenticated teacher (role
  gate depends on the open question above) lists unreviewed placements.
- `POST /api/curriculum/placements/{id}/review/` — teacher sets
  `recommended_level`, which stamps `reviewed_by` and `reviewed_at`.
- `GET /api/curriculum/placements/mine/` — student sees their own placement
  status and result per track.

## Acceptance criteria

1. Migrations apply cleanly on top of Phase 1's, fresh DB.
2. A test creates a Track with three Levels and asserts they come back
   correctly ordered from the API.
3. A test submits a placement with audio, asserts it's `pending` with no
   `recommended_level`.
4. A test submits `skipped_as_beginner=true`, asserts it's immediately
   `reviewed` with `recommended_level` set to that track's order=1 level,
   and `reviewed_by` is null.
5. A test asserts submitting both an audio file and
   `skipped_as_beginner=true` in the same request is rejected.
6. A test asserts a second placement request for a track the student
   already has a pending or reviewed result for updates the existing row
   rather than creating a second one.
7. A test asserts a non-teacher (student, parent) cannot access the review
   endpoint.
8. `python manage.py check` and the full test suite pass.

## Explicitly out of scope for this phase

Cohorts, bookings, scheduling, video, rubric-based session assessment
(distinct from placement — don't reuse or reference the assessment rubric
here, placement is a one-time leveling step, not ongoing scoring), payouts,
pricing. Do not add a `Booking` FK to `PlacementResult` "for later" — if
Phase 3 needs to reference a placement, that FK gets added in Phase 3's
migration, not this one.
