# Quran Academy Backend Correction — B02

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

# 5. PHASE B02 — FIX MEMBERSHIP VS STUDENT ENROLLMENT

## Problem

Current concepts are overlapping:

```text
OrganizationMembership
StudentEnrollment
```

but there is no sufficiently explicit relationship between "student participates in the academy" and "student is recognized as belonging to the academy".

The roadmap requires an academy-specific enrollment lifecycle.

## Current design to preserve

Keep:
- global `User`
- global `ParentLink`
- `OrganizationMembership`

Introduce a proper academic relationship.

## Goal

A student should have a clear academy-specific record that can answer:

```text
academy
student
programme/track
level/placement
status
```

The exact schema can be adapted to existing domain models, but do not create a second independent access mechanism.

## Inspect

```text
organizations/models.py
organizations/serializers.py
organizations/views.py
curriculum/models.py
scheduling/models.py
assessment/models.py
pricing/models.py
```

Search:

```bash
rg -n "StudentEnrollment|OrganizationMembership|student.*organization|organization.*student" .
```

## Required design decision

Decide explicitly:

### Option A — Membership + Enrollment

Use membership as academy participation and enrollment as academic placement.

This is preferred if product semantics require a student account to exist in the academy even before an active programme assignment.

### Option B — Enrollment is a specialised participation relation

If the project decides that student membership is represented by enrollment, document the rule and ensure all authorization helpers understand it.

Do not leave both concepts partially responsible.

## Required invariants

At minimum:

```text
StudentEnrollment.user.role == student
StudentEnrollment.organization == academy owning the enrollment
StudentEnrollment does not cross academy boundaries
Academic relationship cannot point to another academy's track/level
```

If an enrollment references track/level:

```text
enrollment.organization == track.organization
```

must always hold.

## Important

Do not add redundant `organization` columns to many models merely for convenience.

Prefer one canonical ownership path unless a direct organization key is needed for query performance or independent lifecycle.

If a direct organization key is added, explicitly validate that it agrees with the parent relation.

## Tests

Prove:

```text
student A enrolls in academy A
student A cannot be treated as enrolled in academy B
academy A cannot read academy B enrollment
track from A cannot be paired with enrollment B
level from A cannot be paired with enrollment B
suspended/inactive enrollment behavior is deterministic
```

## Acceptance

The team should be able to explain in one sentence:

> "Membership means X; enrollment means Y."

Write that sentence into the model documentation.

---
