# Phase 7 — Assessment & Progress

## Goal

Make delegation to sub-teachers measurable.

After a teaching session, the teacher records the student's performance against a shared rubric for that track. The system stores scores, lets the teacher flag a session for lead review, gives the lead academy-wide quality visibility, and produces periodic progress snapshots for students and linked parents.

This phase creates the quality-control evidence. It does **not** change routing yet.

## Why this phase now

The MVP defines assessment as the mechanism that makes delegation to sub-teachers safe: every teacher uses the same track-specific rubric, the lead can inspect quality, and students/families see progress rather than raw operational data.

Phase 4 deliberately kept sub-teacher ranking capacity-only until assessment data existed. This phase creates that data. A later phase may decide how assessment should influence routing once enough real data exists.

## Scope

### 1. Track-specific rubrics

Add a new `assessment/` Django app.

**AssessmentRubric**
- `track`: OneToOne FK to `curriculum.Track`
- `name`: short human-readable name
- `description`: optional text
- `active`: boolean, default True

**AssessmentCriterion**
- `rubric`: FK to `AssessmentRubric`
- `name`: short criterion name
- `description`: optional text
- `order`: positive integer
- `active`: boolean, default True

Rules:

- A track has at most one active rubric.
- Criterion order is unique within a rubric.
- Active criteria define what new assessments must score.
- Deactivating or renaming a criterion affects future assessments only.
- Existing assessments must remain understandable after rubric edits.
- Do not introduce a full rubric-version graph in this phase unless snapshots prove insufficient; stop and ask before inventing one.

Typical criteria may include:

- Tajweed: makhraj accuracy, madd rules, waqf/ibtida, fluency.
- Hifz: retention, accuracy, fluency, revision consistency.
- Arabic: vocabulary, grammar, comprehension, expression.
- Noorani Qaida: letter recognition, makhraj, harakat, fluency.

These are examples, not hard-coded scoring rules. Rubric content is data owned by the lead/admin.

### 2. Per-session assessment

One completed `Booking` has at most one assessment.

**SessionAssessment**
- `booking`: OneToOne FK to `scheduling.Booking`
- `student`: FK to `accounts.User`
- `track`: FK to `curriculum.Track`
- `assessed_by`: FK to `accounts.User`
- `assessed_at`: datetime
- `teacher_summary`: optional text
- `flagged_for_review`: boolean, default False
- `flag_reason`: optional text
- `lead_reviewed_at`: nullable datetime
- `lead_reviewed_by`: nullable FK to `accounts.User`
- `lead_review_note`: optional text
- `rubric_name`: snapshot of rubric name used
- `criteria_snapshot`: JSON snapshot of criterion identity/name/order used

**AssessmentScore**
- `assessment`: FK to `SessionAssessment`
- `criterion`: FK to `AssessmentCriterion`
- `criterion_name`: snapshot string
- `score`: integer 1–5
- `comment`: optional text

Score meaning:

| Score | Meaning |
|---|---|
| 1 | Needs significant improvement |
| 2 | Developing |
| 3 | Meets expected level |
| 4 | Strong |
| 5 | Excellent |

Do not infer or mutate a student's level from scores in this phase.

### 3. Assessment authorization

A teacher may assess only a booking they taught.

- `assessed_by` must equal `booking.teacher`.
- The teacher must be an approved lead or sub-teacher.
- Students and parents cannot assess.
- A teacher cannot assess another teacher's booking.
- Assessment creation requires booking status `completed`.
- `cancelled` and `no_show` bookings cannot be assessed.
- There is one assessment per booking.

Do not automatically mark a booking completed as a side effect of assessment submission.

### 4. Assessment creation rules

When an assessment is submitted:

1. Load the active rubric for `booking.level.track`.
2. Require exactly one score for every active criterion.
3. Reject unknown criterion ids.
4. Reject duplicate criterion ids.
5. Enforce score range 1–5.
6. Snapshot rubric/criterion names and ordering used at submission time.
7. Calculate the overall average using all criterion scores.
8. Store the teacher summary and optional criterion comments.
9. Allow the teacher to flag the assessment for lead review.

Missing criteria are an error, not an implicit zero.

### 5. Historical rubric behaviour

Rubric configuration is live data; assessments are historical records.

Changing a rubric or criterion affects future assessments only. Existing assessments retain their criterion names/order through snapshots.

Do not silently rewrite historical teacher scores when the live rubric changes.

### 6. Lead review

Flagged assessments enter a lead review queue.

The lead can:

- list flagged, unreviewed assessments;
- open the full assessment and its booking/student/teacher/rubric context;
- add a private review note;
- mark the assessment reviewed.

The lead may view unflagged assessments for monitoring, but only flagged assessments require a review state.

Lead review must not overwrite the teacher's original scores. Review is a separate annotation.

Viewing an assessment does not clear its flag. Clearing/reviewing is an explicit lead action.

### 7. Teacher quality reporting

Lead-only reporting should provide, optionally for a date range and track:

- assessed completed sessions;
- overall average score;
- average score by track;
- assessments flagged;
- flagged assessments still awaiting lead review.

Reports must use only actual assessment data. Missing assessments are never zeroes.

Do not create teacher leaderboards or change routing based on these scores in Phase 7.

### 8. Student and parent progress

Students and linked parents should see progress, not internal quality-control fields.

A progress response per student/track should include:

- track;
- current `recommended_level` from the existing placement result, when present;
- completed assessed sessions in the requested period;
- overall average;
- per-criterion average;
- most recent assessment date;
- recent `teacher_summary` values intended for family visibility.

Never expose `flag_reason` or `lead_review_note` to students or parents.

Parent access must use the existing linked-child permission pattern; unrelated parents cannot query another student's progress.

### 9. Periodic progress snapshots

**ProgressSnapshot**
- `student`: FK to User
- `track`: FK to Track
- `period_start`: datetime
- `period_end`: datetime
- `generated_at`: datetime
- `completed_sessions`: integer
- `assessed_sessions`: integer
- `overall_average`: decimal 0.00–5.00
- `criterion_averages`: JSON snapshot
- `summary`: short text
- `generated_by`: FK to User, nullable
- `visible_to_family`: boolean, default False

Rules:

- Snapshot values are historical and do not change when later assessments arrive.
- Unique `(student, track, period_start, period_end)`.
- Repeating the same generation request must not create a duplicate.
- This phase allows an explicit lead action to generate a snapshot.
- Automatic background scheduling is out of scope.

### 10. Visibility

**Lead**
- academy-wide assessments;
- teacher reports;
- flagged review queue;
- student progress;
- snapshot generation.

**Sub-teacher**
- own submitted assessments;
- own assessment history;
- no academy-wide teacher reports;
- no other teacher's private assessment data;
- no lead review notes unless a later decision widens access.

**Student**
- own assessments/progress;
- no internal flag/review fields;
- no teacher reports.

**Parent**
- linked-child assessments/progress;
- no private review fields;
- no teacher reports.

Permissions must be enforced server-side, not only through serializer field omission.

## API surface

Proposed endpoints:

- `GET /api/assessment/rubrics/?track_id=` — lead/admin view.
- `POST /api/assessment/rubrics/` — lead creates/updates rubric configuration.
- `GET /api/assessment/rubrics/{id}/` — lead reads rubric and criteria.
- `POST /api/assessment/bookings/{booking_id}/` — assigned teacher submits assessment.
- `GET /api/assessment/mine/` — student assessment history.
- `GET /api/assessment/teacher/mine/` — teacher's submitted assessments.
- `GET /api/assessment/review/queue/` — lead flagged-review queue.
- `POST /api/assessment/{id}/review/` — lead reviews/marks reviewed.
- `GET /api/assessment/reports/teachers/` — lead-only teacher quality report.
- `GET /api/assessment/progress/mine/?track_id=` — student progress.
- `GET /api/assessment/progress/child/?student_id=&track_id=` — linked parent progress.
- `POST /api/assessment/snapshots/` — lead generates a snapshot.
- `GET /api/assessment/snapshots/mine/?track_id=` — student family-visible snapshots.
- `GET /api/assessment/snapshots/child/?student_id=&track_id=` — linked parent snapshots.

Exact naming may follow existing repository conventions, but the permissions and behaviours are mandatory.

## Data integrity

Enforce business invariants in model/service code as well as API permissions:

- assessment booking/student/track relationships always agree;
- `assessed_by == booking.teacher`;
- assessed teacher is approved;
- booking is `completed`;
- one assessment per booking;
- one score per criterion per assessment;
- every active criterion is scored exactly once;
- score is 1–5;
- only lead may manage rubric configuration and lead-review annotations;
- historical criterion names remain readable after live rubric edits;
- teacher scores are immutable after submission in this phase;
- parent/student querysets are properly scoped.

Do not use `bulk_create()` where it bypasses these invariants.

## Average calculation

Use one definition everywhere:

`overall_average = sum(all criterion scores) / number of criterion scores`

Per-criterion averages use only scores for that criterion.

Round API presentation consistently, preferably to two decimal places. Do not round individual scores before aggregating.

Missing assessments are absent data, never zero.

## Lifecycle

```text
scheduled
   │
   ├── cancelled/no_show → no assessment
   │
   ▼
completed
   │
   ▼
assessment submitted
   │
   ├── not flagged → complete
   │
   └── flagged → lead review → reviewed
```

Assessment submission does not change booking status.

## Explicitly out of scope

- AI-generated scoring or audio analysis;
- automatic level progression;
- automatic teacher suspension/demotion;
- routing changes using assessment scores;
- teacher leaderboard/ranking;
- payments or payouts;
- new attendance models;
- recurring cohorts;
- automatic snapshot jobs;
- notifications for assessment flags;
- family UI;
- editing another teacher's assessment;
- automatic placement re-review.

## Acceptance criteria

1. Lead can create a track rubric with ordered criteria.
2. Non-lead cannot create/update/delete rubric configuration.
3. Criterion order is unique within a rubric.
4. Historical snapshots remain unchanged after rubric edits.
5. New assessments use only active criteria.
6. Assigned teacher can assess a completed booking.
7. Student, parent, or different teacher is denied assessment submission.
8. Scheduled/cancelled/no-show bookings cannot be assessed.
9. Missing active criterion causes rejection.
10. Duplicate criterion causes rejection.
11. Score outside 1–5 causes rejection.
12. Second assessment for the same booking is rejected.
13. Teacher summary is family-visible; internal flag/review fields are not.
14. Flagged assessments appear in lead review queue.
15. Review adds an annotation and does not overwrite teacher scores.
16. Reviewed assessments leave the pending review queue.
17. Lead teacher report returns counts and averages for date/track filters.
18. Missing assessments never count as zero.
19. Sub-teacher cannot access academy-wide teacher reporting.
20. Student can retrieve own progress by track.
21. Linked parent can retrieve only linked child's progress.
22. Unrelated parent is denied.
23. Progress includes criterion averages and session counts without internal review fields.
24. Assessment does not mutate `PlacementResult.recommended_level`.
25. Lead can generate a period snapshot.
26. Snapshot is immutable after generation.
27. Identical snapshot periods do not duplicate.
28. Student/parent can read only family-visible snapshots in their permitted scope.
29. Full Phase 1–6 regression suite still passes.
30. `python manage.py check` passes.
31. `python manage.py makemigrations --check` passes.
32. Fresh PostgreSQL migration succeeds.
33. Tests cover all four role boundaries plus historical rubric behaviour and aggregation.

## Definition of done

The phase is complete when:

1. a lead configures a rubric;
2. an assigned teacher scores a completed lesson;
3. flagged lessons reach the lead;
4. the data feeds teacher-quality reports;
5. students/linked parents see appropriate progress;
6. a lead can generate a stable period snapshot;
7. all scheduling/routing behaviour remains unchanged.

Do not start routing-quality changes because assessment data now exists. Phase 7 creates the evidence; a later phase decides how evidence influences routing.
