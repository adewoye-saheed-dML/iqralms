# IQRA LMS Backend Correction Progress

| Phase | Status | Evidence |
|---|---|---|
| B00 | DONE | `python manage.py check` (0 issues), `showmigrations` audited, `spectacular --validate` passed (code 0), repo state & readers inventoried, zero test factories in prod |
| B01 | DONE | `active_membership` & `OrganizationMembershipQuerySet.active` require active organization; 280 tests passed in `organizations` & `accounts`; `check` & `spectacular --validate` passed (code 0) |
| B02 | DONE | Downstream student audit complete; parent-child visibility enforced via `ParentLink` + `StudentEnrollment.objects.active()`; 1170 tests passed in `accounts`, `organizations`, `scheduling`, `assessment`; `check` passed (code 0) |
| B03 | DONE | Atomic acceptance implemented, existing membership rejected with 400; 13 lifecycle edge cases added in `InvitationLifecycleAPITests`; 195 tests passed in `organizations` & `notifications`; `check` passed (code 0) |
| B04 | DONE | 0 production factory imports / fallbacks across all apps; org context strictly required; 571 tests passed in `scheduling`; `check` passed (code 0) |
| B05 | DONE | Explicit route `/api/scheduling/organizations/{id}/...` enforced; cross-tenant combinations rejected; concurrency protections preserved; 571 tests passed in `scheduling`; `check` passed (code 0) |
| B06 | DONE | `TeacherTrack` is the single source of truth for teaching eligibility; runtime fallback to `TeacherProfile.specialties` completely removed; 275 tests passed in `curriculum`; `check` passed (code 0) |
| B07 | DONE | `OrganizationTeacherConfiguration` enforced for `hourly_payout_rate`, `approved`, `max_weekly_hours`; `applicable_rate` reads per-academy configuration; multi-academy rates & suspended/unapproved teacher tests added; 833 tests passed in `accounts`, `scheduling`, `payouts`; `check` passed (code 0) |
| B08 | DONE | Payout and pricing financial isolation audited; cross-tenant teacher lookup rejected with 400; multi-academy teacher separate generation, rates, statements & mine verified; 215 tests passed in `payouts` & `pricing`; `check` passed (code 0) |
| B09 | DONE | `spectacular --validate` passed (code 0); student list/detail and invitation list/create/accept responses now expose real typed schemas (`StudentList`, `StudentDetail`, `OrganizationInvitation`, `OrganizationInvitationCreate`); 146 tests passed in `organizations`; `check` passed (code 0) |
| B10 | PENDING | Awaiting execution |
