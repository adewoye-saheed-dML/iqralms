# Quran Academy Backend Correction — B09

**Repository:** `https://github.com/adewoye-saheed-dML/quran_acad`
**Execution mode:** Antigravity CLI — one phase only.

## Agent rules

1. Work only on this phase. Do not start frontend work.
2. Inspect the repository's existing OpenAPI generation workflow before running or changing anything.
3. Do not manually edit generated schema just to make it look correct.
4. Run tests relevant to the changed API contracts.
5. Report the generated-file changes and exact verification commands.
6. Stop after this phase.

---

# 12. PHASE B09 — REGENERATE OPENAPI AFTER BACKEND CHANGES

Run backend OpenAPI generation after all backend contract changes.

Use the repository's existing command rather than inventing a new one.

Inspect:

```text
config/
openapi/
README.md
CLAUDE.md
```

Then verify the generated schema contains the actual response and request shapes for:
- organizations
- memberships
- invitations
- students/enrollments
- curriculum
- scheduling
- payouts
- notifications

## Acceptance

The schema must not be manually edited to hide implementation problems.

The correct order is:

```text
backend implementation
→ backend tests
→ OpenAPI generation
→ schema verification
→ frontend consumption
```

---
