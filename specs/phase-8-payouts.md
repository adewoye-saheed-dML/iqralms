# Phase 8 — Payouts & Statements

## Goal

Turn completed teaching sessions into transparent, auditable teacher payout records and simple teacher statements.

Phase 8 answers:

> "How much does each teacher earn from the sessions they actually completed?"

It does not collect money from families and does not transfer money to teachers. It creates the internal records needed for later payment execution.

The central product rule is:

```text
Family pricing ≠ Teacher payout
```

A family's negotiated price, discount, premium, or pricing agreement must not silently change the teacher's payout.

## Why this phase now

The platform already has:

- users and teacher profiles;
- levels and curriculum;
- bookings;
- routing;
- pricing agreements;
- preferred-teacher waitlists;
- assessment and progress data.

Completed bookings are now the factual source for teaching activity. Phase 8 adds the financial record that summarizes that activity for teachers and the lead.

## Scope

### 1. Teacher payout rate

Use the existing teacher profile/payout-rate mechanism where practical rather than creating a duplicate source of truth.

A payout rate represents what a teacher earns for an eligible teaching session.

Rules:

- Lead controls teacher payout rates.
- A payout rate is independent of the family's negotiated price.
- A rate change affects future payout generation.
- Historical finalized payouts retain the rate that was actually used.
- Do not let students, parents, or sub-teachers modify payout rates.
- Do not make assessment scores automatically modify payout rates.

If the existing `TeacherProfile.hourly_payout_rate` is insufficient for the business rule, stop and ask before introducing a second competing rate model.

### 2. Payout eligibility

A booking may contribute to payout only when it represents an eligible completed teaching session.

For Phase 8:

- `completed` booking → eligible;
- `cancelled` booking → not eligible;
- `no_show` booking → not eligible;
- `scheduled` booking → not yet eligible;
- a booking with no assigned teacher → not eligible;
- a booking that violates existing booking invariants must not be made payout-eligible by bypassing those invariants.

Do not change booking status as a side effect of payout generation.

Use the existing booking model/status rules rather than creating a second definition of "completed."

### 3. Payout record

Introduce a payout record representing one teacher's earned amount for one eligible teaching session.

Recommended shape:

**TeacherPayout**

- `teacher`: FK to `accounts.User`;
- `booking`: OneToOne FK to `scheduling.Booking`;
- `rate_used`: Decimal;
- `amount`: Decimal;
- `currency`: explicit currency code, preferably inherited from one clearly defined existing product convention if available;
- `status`: lifecycle state;
- `created_at`: datetime;
- `finalized_at`: nullable datetime.

The exact field names may follow repository conventions.

Important rule:

One eligible booking must not create two payout records.

The payout record must preserve the actual rate used and amount calculated at generation time.

Do not calculate historical payout amount from the teacher's current rate after the record has been finalized.

### 4. Payout calculation

For the initial MVP payout rule:

```text
payout amount = eligible completed session duration × teacher payout rate
```

Use the booking's actual stored session duration, not the family's price.

Do not silently introduce bonuses, penalties, assessment multipliers, cohort-specific overrides, or teacher-ranking adjustments.

For cohort bookings, follow the existing booking/session representation. Do not invent a new attendance or teaching-duration model in Phase 8.

If the existing booking duration semantics cannot represent the payout calculation unambiguously, stop and ask rather than guessing.

### 5. Generation

A lead can generate payout records for a bounded period.

The generation action should:

1. identify eligible completed bookings in the requested period;
2. identify their assigned teachers;
3. determine the applicable payout rate;
4. calculate the amount;
5. create missing payout records;
6. leave existing finalized payout records unchanged;
7. avoid duplicate records if generation is repeated.

Repeating the same generation request must be idempotent with respect to already-created payout records.

Generation must not create records for cancelled/no-show/scheduled bookings.

### 6. Finalization

A payout becomes historical once finalized.

Recommended states:

```text
generated → finalized
```

Keep the lifecycle intentionally small.

Rules:

- generated payout may be reviewed by the lead;
- finalized payout is immutable;
- teacher may read their own finalized records;
- changing current payout rates does not rewrite finalized records;
- deleting historical payout records should not be part of normal application behaviour.

If a correction process is needed later, record it as an explicit future phase rather than silently editing history.

### 7. Statements

A statement groups payout records for one teacher and one period.

A statement should expose at minimum:

- teacher;
- period start;
- period end;
- eligible session count;
- total payout amount;
- currency;
- statement status;
- generated/finalized timestamp where applicable.

A statement is a reporting view over payout records, not a replacement for those records.

Do not duplicate financial facts unnecessarily.

### 8. Teacher access

A sub-teacher may:

- view their own payout records;
- view their own statements;
- view the session references needed to understand how the total was produced.

A sub-teacher may not:

- view another teacher's payout;
- edit their payout rate;
- edit a finalized payout;
- finalize their own payout;
- see private family pricing agreements unless another existing permission already grants that data.

The lead may manage academy-wide payout records and statements.

Students and parents have no payout access.

## API surface

Exact names may follow existing repository conventions.

Suggested endpoints:

```text
GET  /api/payouts/mine/
GET  /api/payouts/mine/?start=&end=
GET  /api/payouts/statements/mine/
GET  /api/payouts/statements/mine/{id}/
GET  /api/payouts/lead/
POST /api/payouts/generate/
POST /api/payouts/{id}/finalize/
```

A separate teacher-rate endpoint is not required if the existing teacher profile/admin workflow is sufficient.

The lead's generation endpoint should accept a bounded period and should be safe to repeat.

The API must not expose unrelated teachers' financial records.

## Data integrity

Enforce these rules in domain/model/service code as well as through API permissions:

- payout references a real booking;
- payout teacher agrees with the booking teacher;
- only eligible completed bookings can produce payouts;
- one payout per booking;
- rate used is captured at payout creation;
- amount is calculated from the payout rule;
- finalized payout cannot be edited;
- repeated generation does not duplicate payouts;
- current rate changes do not alter finalized historical payouts;
- family pricing does not determine payout;
- assessment does not determine payout;
- teacher querysets are correctly scoped.

Do not use `bulk_create()` where it bypasses required invariants.

## Financial calculation rules

Use one payout calculation definition everywhere:

```text
amount = eligible session duration × applicable teacher payout rate
```

Do not introduce rounding at multiple stages.

Use `Decimal`, not floating-point arithmetic, for financial values.

Round the final monetary result according to the repository's chosen currency convention.

If currency/rounding is currently undefined, stop and ask before introducing a financial convention that could later conflict with payment processing.

## Period boundaries

Periods must have an unambiguous boundary.

Prefer:

```text
period_start = inclusive
period_end   = exclusive
```

Store datetimes in UTC.

A booking belongs to a payout period based on the stored booking start time, not on the time someone later generated the payout.

Do not let local timezone presentation change which bookings belong to a period.

## Historical behaviour

Example:

```text
Teacher rate on January 1  = 5000/hour
Booking on January 5       = 1 hour completed
Payout generated           = 5000

Teacher rate changes later = 7000/hour

January 5 payout           = still 5000
New eligible booking       = uses 7000
```

Changing a current teacher rate must never recalculate an already-finalized payout.

## Relationship with pricing

Example:

```text
Family negotiated price = 3000
Teacher payout rate     = 5000/hour
Completed duration      = 1 hour

Teacher payout          = 5000
```

The family pricing agreement is not the teacher payout calculation.

Likewise:

```text
Family negotiated price = 10000
Teacher payout rate     = 5000/hour
Completed duration      = 1 hour

Teacher payout          = 5000
```

Do not derive teacher earnings from what the family paid.

## Relationship with assessment

Assessment data exists to measure teaching quality.

Phase 8 does not use it to:

- increase payout;
- decrease payout;
- rank teachers;
- suspend teachers;
- change teacher rates automatically.

If performance-based compensation becomes desirable, it gets its own explicit product decision and phase.

## Visibility

### Lead

- academy-wide payout records;
- payout generation;
- payout finalization;
- teacher-rate management through the existing approved workflow;
- statements for any teacher.

### Sub-teacher

- own payout records;
- own statements;
- enough session detail to reconcile their own total.

### Student

- no payout access.

### Parent

- no payout access.

Permissions must be enforced server-side.

## Explicitly out of scope

- Paystack/Stripe/payment gateway integration;
- bank transfers;
- automatic payout execution;
- tax calculations;
- invoices;
- receipts;
- accounting exports;
- currency conversion;
- bonuses;
- penalties;
- assessment-based compensation;
- teacher leaderboard/ranking;
- automatic rate changes;
- automatic background jobs;
- email/SMS/WhatsApp payout notifications;
- family-facing financial UI;
- correction/reversal workflows for finalized payouts.

## Acceptance criteria

1. Lead can manage the applicable teacher payout rate through the approved teacher workflow.
2. A completed eligible booking can produce exactly one payout record.
3. Cancelled bookings do not produce payouts.
4. `no_show` bookings do not produce payouts.
5. Scheduled bookings do not produce payouts.
6. A payout stores the rate used for that payout.
7. Payout amount is calculated from eligible session duration × payout rate.
8. Family pricing has no effect on the payout amount.
9. Assessment scores have no effect on the payout amount.
10. Repeating payout generation does not duplicate existing payout records.
11. Finalized payout records cannot be edited.
12. A later payout-rate change does not change an existing finalized payout.
13. Lead can generate payout records for a bounded period.
14. Teacher can retrieve only their own payout records.
15. Teacher can retrieve only their own statements.
16. Teacher cannot finalize or edit another teacher's payout.
17. Student cannot access teacher payout data.
18. Parent cannot access teacher payout data.
19. Statement totals agree with the underlying payout records.
20. Period boundaries are deterministic and use UTC booking start times.
21. Payout generation does not alter booking status.
22. Existing scheduling/routing/pricing/assessment behaviour remains unchanged.
23. `python manage.py check` passes.
24. `python manage.py makemigrations --check` passes.
25. Fresh PostgreSQL migration succeeds.
26. Targeted automated tests cover the financial and authorization invariants.
27. Developer manually verifies the lead payout-generation journey.
28. Developer manually verifies the teacher statement journey.
29. Developer manually verifies that historical payout values survive a rate change.
30. Developer manually verifies that family pricing and assessment data do not alter payout.

## Suggested implementation breakdown

Keep implementation deliberately small.

### 8.1 Confirm payout-rate source

Inspect the existing `TeacherProfile.hourly_payout_rate`.

Do not create a second payout-rate model unless the existing field cannot satisfy the business rule.

### 8.2 Payout model

Add the smallest model needed to represent one teacher payout for one eligible booking.

### 8.3 Payout calculation service

Implement one focused function/service for:

```text
booking + applicable rate → payout amount
```

Reuse existing booking duration semantics.

### 8.4 Generation

Implement lead-controlled generation for a bounded period.

Make it idempotent.

### 8.5 Finalization

Add the smallest lifecycle needed to make payout history immutable.

### 8.6 Teacher statement endpoint

Expose teacher-scoped payout history and period totals.

### 8.7 Lead management

Expose lead-only generation/finalization controls.

### 8.8 Manual acceptance

The developer manually tests the complete flow in Swagger/client before Phase 8 is marked complete.

## Manual testing checklist

After implementation, manually verify:

```text
Lead
  → create/change teacher payout rate
  → generate payout period
  → inspect generated payout
  → finalize payout
  → regenerate same period
  → confirm no duplicate

Teacher
  → open own statement
  → inspect contributing sessions
  → confirm total

Security
  → teacher attempts another teacher's payout
  → student attempts payout endpoint
  → parent attempts payout endpoint

Historical behaviour
  → finalize payout at old rate
  → change teacher rate
  → confirm old payout unchanged
  → generate later payout
  → confirm new rate is used

Separation
  → change family pricing agreement
  → confirm payout unchanged

Assessment
  → change/submit assessment data
  → confirm payout unchanged
```

## Definition of done

Phase 8 is complete when:

1. eligible completed teaching activity can be converted into payout records;
2. generation is repeat-safe;
3. finalized payout history is immutable;
4. teachers can see only their own payout history/statements;
5. the lead can manage payout generation/finalization;
6. family pricing remains separate from teacher payout;
7. assessment remains separate from payout;
8. manual end-to-end verification passes;
9. checks and targeted automated tests pass;
10. important decisions are recorded in `learnings.md`;
11. deliberate shortcuts are recorded in `tech-debt.md`;
12. the phase is committed before Phase 9 begins.

## Stop and ask instead of guessing

Stop before implementation when:

- the existing `hourly_payout_rate` cannot support the required calculation;
- payout currency is unclear;
- rounding rules are unclear;
- cohort duration semantics are unclear;
- rate-change timing is ambiguous;
- corrections to finalized payouts are required;
- someone proposes using family price, assessment score, or routing outcome to alter payout;
- a new financial workflow would require payment execution or accounting infrastructure.

Routine model, serializer, permission, admin, and endpoint implementation may proceed when the rule is already explicit.
