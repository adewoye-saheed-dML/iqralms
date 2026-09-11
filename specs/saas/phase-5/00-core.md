# Task 5.0 — Core

## Goal

Convert pricing agreements from a globally accessible domain into an organization-scoped domain without changing the product into a payment or billing system.

## Invariants

- Every agreement resolves to one academy through its level.
- Student and level belong to the same academy.
- Approver is an eligible user in that academy.
- Querysets, serializers, permissions, and mutations respect organization context.
- Cross-tenant IDs cannot bypass isolation.
- Historical agreements remain auditable.
