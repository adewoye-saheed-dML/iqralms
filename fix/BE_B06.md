# Quran Academy Backend Correction — B06

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

# 9. PHASE B06 — REQUIRE EXPLICIT ORGANIZATION CONTEXT IN SCHEDULING

## Problem

Some scheduling code still attempts to infer an organization when one is absent.

That is incompatible with the SaaS tenant model.

## Goal

The operation should always know:

```text
Which academy is this schedule for?
```

## Inspect

```text
scheduling/models.py
scheduling/serializers.py
scheduling/views.py
scheduling/services.py
scheduling/routing.py
```

Search:

```bash
rg -n "organization=None|len\\(active_memberships\\)|multiple_organizations|organization is None" scheduling
```

## Fix

For tenant-owned scheduling operations:

```text
URL/context organization
→ membership verification
→ serializer/service gets verified organization
→ model validation
```

The backend may derive organization from a canonical child object where that is safe, but it must never choose arbitrarily between multiple tenants.

## Important

Do not make the frontend responsible for preventing cross-tenant scheduling.

The backend must remain safe even if the frontend sends an academy ID copied from another browser tab.

## Tests

```text
academy A teacher + academy B level -> rejected
academy A student + academy B teacher -> rejected
academy A booking cannot use academy B cohort
academy A booking cannot use academy B level
academy A availability cannot be created under academy B
```

---
