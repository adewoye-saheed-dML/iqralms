# Quran Academy Backend Correction — B01

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

# 4. PHASE B01 — FIX TENANT ACTIVATION SEMANTICS

## Problem

`Organization` has an `is_active` field, but `active_membership()` currently uses membership status without also treating organization activation state as part of access semantics.

That creates the possibility of:

```text
Organization.is_active = False
OrganizationMembership.status = active
```

while the membership helper still grants tenant access.

## Goal

Establish one authoritative answer to:

> "Can this user currently enter this academy?"

The answer must account for:
- authenticated user
- organization existence
- organization operating/active state
- active organization membership

## Backend files to inspect

Start with:

```text
organizations/models.py
organizations/permissions.py
organizations/views.py
accounts/tenancy.py
organizations/tests/
```

Search:

```bash
rg -n "def active_membership|is_active|MembershipStatus" organizations accounts
```

## Required implementation

Make one function authoritative.

Preferred conceptual contract:

```python
active_membership(user, organization)
```

returns a usable membership only when:
- user is authenticated
- organization exists
- organization is active
- membership is active

Do not scatter organization-active checks through every view.

## Tests required

Add cases for:

```text
active org + active membership -> access
inactive org + active membership -> denied
active org + suspended membership -> denied
inactive org + suspended membership -> denied
anonymous -> denied
wrong organization -> denied
```

Verify both:
- permission helpers
- a real API endpoint

## Acceptance gate

Do not proceed until:

```bash
python manage.py test organizations accounts
```

passes.

---
