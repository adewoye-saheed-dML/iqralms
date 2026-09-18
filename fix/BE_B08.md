# Quran Academy Backend Correction — B08

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

# 11. PHASE B08 — CLEAN STALE BACKEND DOCUMENTATION

## Problem

Some code comments/docstrings say things such as:

```text
"Nothing reads this yet"
```

even though current code now reads the newer academy-scoped configuration.

This causes future agents to make incorrect changes.

## Goal

The codebase should describe the current architecture, not historical phase boundaries.

## Search

```bash
rg -n "Nothing reads this yet|not yet|legacy|compatibility|until scheduling tenancy|Phase [0-9]" accounts scheduling curriculum organizations
```

## Rule

Every major domain file should clearly state:
- authoritative fields
- compatibility fields
- transition status
- intended end state

Do not remove useful historical reasoning if it still explains a constraint.

Just update statements that are factually false.

---
