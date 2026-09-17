# Quran Academy Backend Correction — B07

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

# 10. PHASE B07 — FINISH TEACHER SPECIALTY MIGRATION

## Problem

Two concepts remain:

```text
TeacherProfile.specialties
TeacherTrack
```

`TeacherTrack` is academy-scoped and therefore suitable for SaaS.

`TeacherProfile.specialties` is global and therefore cannot express academy-specific teaching authority.

## Goal

Create one authoritative academy-scoped teaching-assignment concept.

Preferred:

```text
OrganizationMembership
    ↓
TeacherTrack
    ↓
Track
```

## Inspect

```bash
rg -n "specialties|TeacherTrack|specialty_error|matching_sub_teachers|TeacherProfile" .
```

Especially inspect:

```text
accounts/models.py
curriculum/models.py
scheduling/models.py
```

## Required migration strategy

Do not delete the legacy field blindly.

Use a controlled transition:

```text
1. Identify all readers.
2. Switch all booking/routing readers to TeacherTrack.
3. Migrate any valid legacy assignments where possible.
4. Add regression tests.
5. Remove legacy readers.
6. Decide whether the old field should be removed or retained as explicitly non-authoritative legacy data.
```

## End-state requirement

There must be one answer to:

> "Can teacher T teach track X in academy A?"

That answer must include the academy.

## Tests

```text
teacher assigned track in academy A -> can teach A
same teacher not assigned track in B -> cannot teach B
same track name in A and B -> independent
global legacy specialty cannot grant academy B access
```

---
