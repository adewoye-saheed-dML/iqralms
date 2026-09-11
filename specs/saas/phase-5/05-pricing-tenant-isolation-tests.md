# Task 5.5 — Pricing Tenant-Isolation Tests

Create two academies with students, levels, leads, and agreements in each.

Test:

1. Academy A list cannot return Academy B agreements.
2. Academy A cannot retrieve Academy B agreement by ID.
3. Academy A student cannot access Academy B through `/mine/`.
4. Academy A lead cannot mutate Academy B agreement.
5. Cross-academy student/level creation is rejected.
6. Cross-academy approver is rejected.
7. Legacy unscoped endpoints cannot expose another academy's data.
8. Serializers do not leak tenant-private fields.
9. Empty and mixed-tenant querysets remain isolated.
10. Direct object IDs cannot bypass permissions.

Keep regression tests for valid same-academy pricing workflows.
