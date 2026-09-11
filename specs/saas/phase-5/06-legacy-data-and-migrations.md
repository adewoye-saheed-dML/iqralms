# Task 5.6 — Legacy Data and Migrations

Audit existing `PricingAgreement` rows for missing relations, mismatched student/level academy, invalid approvers, duplicate active agreements, and other violations of the Phase 5 invariants.

Prefer non-destructive migrations. Do not silently delete invalid historical rows; document and remediate them using the repository's data policy.

Verify both fresh and upgraded databases with:

```bash
python manage.py makemigrations --check
python manage.py migrate
```

against PostgreSQL.
