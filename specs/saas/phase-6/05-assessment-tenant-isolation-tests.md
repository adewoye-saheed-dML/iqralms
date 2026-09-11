# Task 6.5 — Assessment Tenant-Isolation Tests

## Objective

Prove assessment isolation through adversarial multi-academy tests.

## Required test setup

Create at least:

- Two organizations.
- Members belonging to each organization.
- Curriculum objects in each organization.
- Students in each organization.
- Teachers with different academy memberships.
- Assessment resources and submissions in both organizations.
- Parent-child links where applicable.

## Required assertions

- Academy A cannot list Academy B assessment records.
- Academy A cannot retrieve Academy B records by ID.
- Academy A cannot create records using Academy B foreign keys.
- Academy A cannot submit or grade Academy B work.
- Students cannot access other students' assessment data.
- Parents cannot access unrelated children's assessment data.
- Teachers cannot access assessments outside authorized academies.
- Inactive memberships are rejected.
- Legacy routes cannot bypass organization-scoped routes.
- Reports and nested endpoints remain isolated.

## Completion criteria

Tests pass against PostgreSQL and fail if organization filters or object-level checks are removed.
