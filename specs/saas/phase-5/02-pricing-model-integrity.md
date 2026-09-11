# Task 5.2 — Pricing Model Integrity

## Required rules

For every agreement:

```text
student.organization == level.track.organization
```

The approver must have the required lead role and active membership/authorization in the same academy.

Preserve historical agreement values and the existing active/inactive business rule. Maintain the intended one-active-agreement rule for the same student and level unless the current domain explicitly defines another rule.

## Acceptance

- Same-academy agreement succeeds.
- Cross-academy student/level combination fails.
- Cross-academy approver fails.
- Invalid duplicate active agreement is rejected.
- Historical agreements remain accessible to authorized users.
