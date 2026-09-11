# Task 5.3 — Pricing API Tenancy

Move pricing access to organization-scoped routes:

```text
/api/pricing/organizations/<organization_pk>/agreements/
/api/pricing/organizations/<organization_pk>/agreements/mine/
```

List, retrieve, create, update, deactivate, and `mine` must enforce tenant context server-side.

Creation validates academy context, student membership, level ownership, student/level academy match, and approver authorization.

Legacy routes such as `/api/pricing/agreements/` and `/api/pricing/agreements/mine/` must be retired, blocked, or made unable to bypass the new rules.
