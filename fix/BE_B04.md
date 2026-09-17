# Quran Academy Backend Correction — B04

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

# 7. PHASE B04 — BUILD A REAL INVITATION LIFECYCLE

## Problem

Teacher "invitation" currently behaves approximately as:

```text
create membership
→ membership is active
→ send invitation notification
```

This is backwards.

## Goal

Implement:

```text
create invitation
→ pending
→ recipient accepts
→ membership becomes active
```

## Inspect

```text
organizations/models.py
organizations/serializers.py
organizations/views.py
notifications/
accounts/
```

Search:

```bash
rg -n "notify_teacher_invitation|invitation|OrganizationMembership" .
```

## Invitation model fields

Minimum conceptual fields:

```text
organization
recipient email/phone/user reference as appropriate
intended role
token or token digest
expires_at
accepted_at
status
created_at
```

Do not store a raw reusable secret unnecessarily.

## Required lifecycle

```text
pending
accepted
expired
revoked 
```

Avoid activating a membership before acceptance unless a very explicit business rule requires it.

## Important design case

What if invited user does not yet have an account?

The invitation must support that scenario without inventing duplicate identities.

Possible flow:

```text
invitation created
→ user registers/logs in
→ invitation token matched
→ invitation accepted
→ membership created/activated
```

## Role safety

The invitation's intended organization role must be one of the assignable roles.

Never allow invitation input to create an owner.

## Tests

```text
valid invitation accepted
expired invitation rejected
reused invitation rejected
wrong academy invitation rejected
wrong user/token rejected
already-member handling is deterministic
non-assignable role rejected
acceptance creates/activates correct membership
```

## Acceptance

A teacher must not gain academy access merely because the admin clicked "Invite".

---
