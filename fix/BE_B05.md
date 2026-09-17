# Quran Academy Backend Correction — B05

**Repository:** `https://github.com/adewoye-saheed-dML/quran_acad`
**Execution mode:** Antigravity CLI — one phase only.

## Agent rules

1. Work only on this phase. Do not start later phases.
2. Inspect the current repository before editing. Do not assume the playbook matches the current code exactly.
3. Preserve existing architecture unless this phase explicitly requires a change. Do not rewrite unrelated apps.
4. Add or update tests for every behavior changed.
5. Run targeted tests first, then the relevant regression suite.
6. Do not mark the phase complete merely because files changed. Completion requires the acceptance gate in this file.
7. Show the final diff summary and the exact commands/tests run.
8. Stop after this phase. Do not proceed automatically to the next phase.

---

# 8. PHASE B05 — REMOVE TEST FACTORY IMPORTS FROM PRODUCTION SCHEDULING

## Critical issue

`Availability.create_from_local()` contains imports from test factories.

This must be removed.

## Search

```bash
rg -n "tests.factories|OrganizationFactory|ensure_teacher_configured" scheduling accounts organizations
```

## Required behavior

Production code must never do:

```python
from organizations.tests.factories import ...
```

or equivalent.

If organization context is missing:

```text
raise validation error
```

or require the caller to provide it.

Never:
- create a tenant automatically
- fabricate test data
- assume a fake academy
- silently switch tenant

## Preferred contract

Make organization required where the operation is inherently tenant-owned:

```python
Availability.create_from_local(
    organization=organization,
    teacher=teacher,
    ...
)
```

Then validate:

```text
teacher has active membership in organization
teacher has valid academy configuration
```

## Backward compatibility

If old internal callers omit organization, migrate those callers in the same phase.

Do not keep the dangerous fallback.

## Tests

```text
missing organization -> deterministic validation failure
teacher in academy A + organization A -> success
teacher in academy A + organization B -> failure
teacher in multiple academies + omitted org -> failure
no test modules imported by runtime scheduling code
```

Use static inspection:

```bash
rg -n "from .*tests|import .*tests" scheduling accounts organizations
```

Any runtime domain import from test modules is a stop-ship finding.

---
