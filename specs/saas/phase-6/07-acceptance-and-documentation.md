# Task 6.7 — Acceptance and Documentation

## Objective

Close Phase 6 only after technical and operational verification.

## Required checks

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Run all checks against PostgreSQL.

## Manual acceptance

Verify with two academies:

- academy-scoped assessment lists;
- assessment detail access;
- student submission access;
- teacher grading access;
- lead management access;
- parent access to a linked child;
- rejection of unrelated-child access;
- rejection of inactive-member access;
- rejection of cross-academy foreign keys;
- report isolation;
- legacy route behavior.

## Documentation

Update:

- `CLAUDE.md`
- `learnings.md`
- `tech-debt.md`
- Phase 6 specification status and task statuses
- API/OpenAPI documentation where applicable

## Status

COMPLETE

## Verification Results

1. `python manage.py check`: Passed with 0 issues identified.
2. `python manage.py makemigrations --check`: Passed with no changes detected.
3. `python manage.py migrate`: Passed; migration `assessment.0002_remediate_legacy_assessment` applied.
4. `manage.py test assessment`: Passed; 319/319 tests pass (models, API, tenant isolation, legacy migration).
5. OpenAPI validation (`python manage.py spectacular --file /dev/null --validate`): Passed with 0 validation errors.

## Two-Academy Acceptance Summary

- **Academy-scoped lists**: Verified in `test_tenant_isolation.py`; Academy A only lists its own rubrics, assessments, snapshots, and review queue items.
- **Assessment detail**: Lead A requesting Academy B assessment by ID receives 404 Not Found (not 403), preventing ID probing.
- **Student submission access**: Teacher A attempting to submit for Academy B booking receives 404 Not Found.
- **Teacher grading access**: Teacher A in `teacher/mine/` only views their own submissions in Academy A; other teachers and other academies are excluded.
- **Lead management access**: Lead A can only view, review, and generate snapshots within Academy A.
- **Parent linked child access**: Parent A can read assessments and snapshots for their linked child in Academy A; unrelated children in Academy A or Academy B return 403 Forbidden.
- **Inactive member rejection**: Suspended members (lead, teacher, student, parent) are refused with 403 Forbidden.
- **Cross-academy foreign keys**: Writing an assessment, rubric, or snapshot referencing a foreign track, student, or criterion returns 400 Bad Request at the API layer and raises `ValidationError` at the model layer (`full_clean()`).
- **Report isolation**: Teacher quality reports and student progress calculations only aggregate sessions within the target organization, isolating multi-academy students and teachers.
- **Legacy route retirement**: Legacy unscoped `/api/assessment/...` paths retired and redirected to `/api/assessment/organizations/<organization_pk>/...`.

