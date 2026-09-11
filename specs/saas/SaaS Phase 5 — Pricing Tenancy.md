# SaaS Phase 5 — Pricing Tenancy

## Status

**PLANNED**

## Objective

Make the existing pricing agreement domain tenant-safe so each academy can manage and view pricing agreements only within its own organization.

This phase does not introduce payment collection, billing, invoices, subscriptions, wallets, or teacher payout processing.

## Ownership model

Preferred path:

```text
PricingAgreement
    -> Level
        -> Track
            -> Organization
```

Do not add a redundant `organization` field to `PricingAgreement` unless the ownership audit proves the derived path is insufficient.

## Core rules

1. An agreement belongs to the academy that owns its level.
2. The student must be an active member of the same academy.
3. Student and level from different academies must never be combinable.
4. The approver must be an eligible lead/member of the same academy.
5. Historical agreements remain auditable.
6. `standard_rate` and `agreed_rate` remain distinct.
7. Private notes are only exposed to authorized users.
8. `/mine/` returns only the requesting student's agreements in the selected academy.
9. Legacy unscoped routes must not provide a bypass.

## Target API

```text
/api/pricing/organizations/<organization_pk>/agreements/
/api/pricing/organizations/<organization_pk>/agreements/mine/
```

Use existing repository naming conventions if an equivalent organization-scoped convention is already established.

## Out of scope

Payments, billing, invoices, subscriptions, wallets, teacher payouts, teacher waitlists, scheduling redesign, and frontend pricing UI.

## Tasks

| Task | Purpose |
|---|---|
| 5.1 | Pricing ownership audit |
| 5.2 | Pricing model integrity |
| 5.3 | Pricing API tenancy |
| 5.4 | Pricing permissions and privacy |
| 5.5 | Tenant-isolation tests |
| 5.6 | Legacy data and migrations |
| 5.7 | Acceptance and documentation |

## Phase gate

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Run the gate against PostgreSQL, then perform manual two-academy acceptance and OpenAPI verification.
