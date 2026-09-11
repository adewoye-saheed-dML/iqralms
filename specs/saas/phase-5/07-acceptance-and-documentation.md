# Task 5.7 — Acceptance and Documentation

Run the final PostgreSQL gate:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py migrate
pytest
```

Perform two-academy manual acceptance for create, list, retrieve, `/mine/`, approval/management, and cross-tenant denial.

Verify the generated OpenAPI schema documents the final organization-scoped pricing routes and serializers.

Update:

- `CLAUDE.md`
- `learnings.md`
- `tech-debt.md`

Document decisions, limitations, migration notes, and deferred work.

Suggested commit:

```bash
git add CLAUDE.md "specs/saas/SaaS Phase 5 — Pricing Tenancy.md" specs/saas/phase-5/
git commit -m "docs(saas): define Phase 5 pricing tenancy"
```

Mark Phase 5 DONE only after implementation, tests, migrations, tenant-isolation acceptance, OpenAPI verification, documentation, and commit are complete.
