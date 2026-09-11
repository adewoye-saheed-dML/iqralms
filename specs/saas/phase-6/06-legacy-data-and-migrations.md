# Task 6.6 — Legacy Data and Migrations

## Objective

Handle historical assessment records without destructive or ambiguous tenant assignment.

## Requirements

- Inspect all existing assessment rows before migration.
- Identify rows with missing, ambiguous, or inconsistent organization ownership.
- Prefer deterministic backfills from existing curriculum, cohort, student, or membership relationships.
- Do not silently assign ambiguous records to an arbitrary academy.
- Preserve historical assessment data and auditability.
- Use reversible or carefully documented data migrations where possible.
- Record unresolved rows and remediation decisions.
- Verify fresh migrations and migration safety against PostgreSQL.

## Acceptance

Every retained assessment record has a defensible organization owner, or is explicitly quarantined and documented for manual remediation.
