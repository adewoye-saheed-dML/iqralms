# Quran Academy Backend Correction — B03

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

# 6. PHASE B03 — FIX PARENT CHILD TENANT VISIBILITY

## Problem

`ParentLink` is correctly global as a family relationship.

However, academy-specific child visibility currently depends on active memberships and does not make the new enrollment model part of the rule.

## Required rule

A parent can see a child in an academy only when:

```text
parent-child ParentLink exists
AND
parent is allowed in academy
AND
child participates in academy
```

"Participates in academy" must use the canonical enrollment/membership rule established in B02.

## Inspect

```text
accounts/tenancy.py
organizations/models.py
organizations/tests/
scheduling/serializers.py
```

Search:

```bash
rg -n "children_in_organization|ParentLink|resolve_requested_student" .
```

## Fix

Refactor:

```python
children_in_organization(parent, organization)
```

to use the canonical student-academy participation rule.

Do not copy the participation rules inline again.

Create one service/queryset helper and reuse it.

## Tests

At least:

```text
linked child + enrolled -> visible
linked child + not enrolled -> invisible
linked child + suspended participation -> invisible
unlinked child + enrolled -> invisible
parent not in academy -> no children visible
child enrolled in academy B -> never visible through academy A
```

---
