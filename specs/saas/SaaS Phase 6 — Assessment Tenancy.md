# SaaS Phase 6 — Assessment Tenancy

## Status

COMPLETE

## Objective

Make the assessment domain tenant-safe so each academy can create, manage, submit, grade, review, and report assessment data only within the academy that owns the relevant curriculum and academic records.

This phase covers assessment tenancy and privacy. It does not introduce a new assessment engine, payment features, billing, subscriptions, or frontend redesign.

## Ownership model

The preferred ownership path is:

```text
Assessment resource
    -> academic/curriculum resource
        -> Track
            -> Organization
```

The exact path must be confirmed during Task 6.1 before schema changes are made. Do not add redundant organization fields until the audit proves they are necessary.

## Core rules

1. Every assessment resource must have a deterministic organization owner.
2. Assessment resources must not combine students, teachers, cohorts, levels, tracks, or curriculum objects from different academies.
3. A student may access only assessment records belonging to an academy in which the student has an active membership.
4. Teachers may access and manage assessment records only within academies where they are active members and have the required teaching or assessment authority.
5. Leads and authorized academy staff may manage assessment records only for their own academy.
6. Parent access must be limited to assessment records of linked children within the relevant academy.
7. Assessment submissions, grades, feedback, attempts, and reports must not leak across academy boundaries.
8. Historical assessment records must remain auditable.
9. Querysets, serializers, permissions, and nested routes must enforce tenancy.
10. Legacy unscoped endpoints must not provide a tenant bypass.

## Scope

- Assessment model ownership audit.
- Assessment, submission, attempt, grade, feedback, and reporting tenancy.
- Organization-scoped API routes.
- Student, teacher, lead, and parent privacy rules.
- Cross-academy validation.
- Legacy data and migration handling.
- Adversarial tenant-isolation tests.
- OpenAPI and manual acceptance documentation.

## Out of scope

Payments, billing, invoices, subscriptions, wallets, teacher payouts, scheduling redesign, pricing redesign, frontend assessment UI, notifications, and analytics beyond tenant-safe assessment reporting.

## Tasks

| Task | Purpose | Status |
|---|---|---|
| 6.1 | Assessment ownership and data-flow audit | COMPLETE |
| 6.2 | Assessment model integrity | COMPLETE |
| 6.3 | Assessment API tenancy | COMPLETE |
| 6.4 | Assessment permissions and privacy | COMPLETE |
| 6.5 | Assessment tenant-isolation tests | COMPLETE |
| 6.6 | Legacy data and migrations | COMPLETE |
| 6.7 | Acceptance and documentation | COMPLETE |

## Phase gate

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Run the gate against PostgreSQL. Also complete manual two-academy acceptance, parent/student privacy checks, teacher authorization checks, and OpenAPI verification.
