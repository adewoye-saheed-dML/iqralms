# Task 6.2 — Assessment Model Integrity

## Objective

Enforce assessment tenancy at the model and service boundaries.

## Requirements

- Validate that assessment resources belong to one organization.
- Validate that linked curriculum objects belong to the same organization.
- Validate that students belong to the assessment organization through active membership.
- Validate that teachers and graders are authorized in the assessment organization.
- Prevent cross-academy combinations during create and update operations.
- Preserve historical attempts, submissions, grades, and feedback.
- Use `full_clean()` or equivalent invariant enforcement where appropriate.
- Add migrations only when the ownership audit requires them.

## Acceptance

Invalid cross-academy combinations must fail consistently through ORM, admin, service, and API writes.
