# SaaS Phase 4 spec — split index

This phase's spec was split from one 1,924-line document into task-scoped files so a Claude Code session only loads what the current task needs, instead of the entire phase spec every time.

**Every session:** read `00-core.md` first (goal, current architecture, what must survive, the ownership model, out-of-scope list, definition of done — small and constant across the whole phase).

**Then read only the one file for the task you're doing:**

| File | Task | Covers |
|---|---|---|
| `4.1-audit.md` | 4.1 Audit only | Consumers to search, no code changes |
| `4.2-ownership-query-layer.md` | 4.2 Ownership/query layer | Tenant query helpers |
| `4.3-availability-tenancy.md` | 4.3 Availability tenancy | Ownership, example, timezone, queryset, legacy migration |
| `4.4-teacher-configuration.md` | 4.4 Teacher scheduling configuration | Approval + weekly capacity via `OrganizationTeacherConfiguration` |
| `4.5-teachertrack-migration.md` | 4.5 TeacherTrack migration | Curriculum eligibility, `specialty_error()` |
| `4.6-booking-cohort-tenancy.md` | 4.6 Booking and cohort tenancy | Ownership, student/parent/teacher rules, overlap, lock, legacy ownership |
| `4.7-routing-tenancy.md` | 4.7 Routing tenancy | Context, order, cohort/lead/sub/preferred-teacher routing |
| `4.8-waitlist-tenancy.md` | 4.8 Waitlist tenancy | Ownership, promotion, queryset, legacy ownership |
| `4.9-api-tenancy.md` | 4.9 API tenancy | Permissions, routes, serializers, API privacy, OpenAPI |
| `4.10-tenant-isolation-tests.md` | 4.10 Tenant isolation tests | Cross-tenant object-id protection + all 7 adversarial scenarios + the 18-item test list |
| `4.11-acceptance-and-docs.md` | 4.11 Acceptance | Migration discipline, PostgreSQL gate, manual journey, documentation |

Every rule in these files was copied verbatim from the original spec — nothing was reworded or summarized, only regrouped by task. The unabridged original is kept one level up at `../SaaS Phase 4 — Scheduling Tenancy (full).md` if you ever need to see the whole thing in one place (e.g. final review before marking the phase DONE).

**Workflow:** one task file per Claude Code session; commit after each task; don't run the full `check`/`migrate`/`pytest`-on-Postgres gate until Task 4.11.
