# IQRA LMS Backend Correction — Implementation Plan

## Phase B00: Baseline Inspection & Repository Inventory (DONE)
- System check, spectacular schema generation/validation, test DB migrations all verified with 0 errors.

## Phase B01: Tenant Access Foundation (DONE)
- Canonical `active_membership` and `OrganizationMembershipQuerySet.active` require both active membership and active organization (`organization__is_active=True`).
- Added test verifying inactive academies are not listed in `mine/`.
- 280 tests passed in `organizations` and `accounts`.

## Phase B02: Membership vs Student Enrollment (DONE)
- Distinguishes authority (`OrganizationMembership`) from academic participation (`StudentEnrollment`).
- Downstream audit verified parent-child visibility and student queries.
- 1170 tests passed in `accounts`, `organizations`, `scheduling`, `assessment`.

## Phase B03: Real Invitation Lifecycle (DONE)
- Atomic acceptance, rejection of existing memberships (no silent re-rolling).
- 13 lifecycle edge cases added in `InvitationLifecycleAPITests`.
- 195 tests passed in `organizations` and `notifications`.

## Phase B04: Eliminate Test-Factory Runtime Fallbacks (DONE)
- Scanned all production code: 0 production factory imports or runtime test state fallbacks.
- Verified absence of organization guessing in production scheduling, pricing, assessment, payouts.
- 571 tests passed in `scheduling`.
- `python manage.py check` passed with 0 issues.

## Phase B05: Explicit Organization Context in Scheduling (DONE)
- Verified all scheduling endpoints use explicit route `/api/scheduling/organizations/{organization_pk}/...`.
- Verified `AcademyScopedView` resolves tenant and caller membership without guessing.
- Verified cross-tenant invariants: foreign levels, teachers, students, bookings, waitlists, and cohorts are rejected.
- Verified concurrency protections (`TeacherBookingLock`, `select_for_update()`, `transaction.atomic()`).
- 571 tests passed in `scheduling`; `check` passed with 0 issues.

## Phase B06: Complete TeacherTrack Migration (DONE)
- Verified `TeacherTrack` is the sole authoritative model for academy-scoped teaching authority across `curriculum` and `scheduling`.
- Confirmed `specialty_error()`, `Booking.clean()`, `Cohort.clean()`, `route_session()`, `matching_sub_teachers()`, and `lead_teacher()` require `TeacherTrack` in organization context.
- Runtime fallback to `TeacherProfile.specialties` is completely removed from all operational code paths.
- 275 tests passed in `curriculum`; `check` passed with 0 issues.

## Phase B07: Academy-Scoped Teacher Configuration Including Payout Rate (DONE)
- `OrganizationTeacherConfiguration` enforced as authoritative for `approved`, `max_weekly_hours`, and `hourly_payout_rate`.
- Updated `payouts/services.py:applicable_rate` to look up active `OrganizationTeacherConfiguration.hourly_payout_rate` in the organization context.
- Fixed `payouts/tests/test_services.py` multi-academy rate tests and rate change tests.
- Verified 833 tests passed in `accounts`, `scheduling`, `payouts`; `python manage.py check` passed with 0 issues.

---

## Phase B08: Payout Tenancy and Financial Isolation Audit (DONE)
- Payout and pricing financial isolation audited; cross-tenant teacher lookup rejected with 400.
- Multi-academy teacher separate generation, rates, statements & mine verified.
- 215 tests passed in `payouts` & `pricing`; `python manage.py check` passed with 0 issues.

---

## Next Phase: B09 — OpenAPI as the Frontend Contract
### Objective
Ensure OpenAPI schema completely and accurately describes the implemented multi-tenant backend without undocumented transports.
Verify response schemas for student list/detail, invitation list/detail/accept, teacher configurations, scheduling, pricing, assessment, payouts, notifications, imports, and audit endpoints.

### Steps
1. Run `python manage.py spectacular --file schema.yml --validate`
2. Audit endpoints in schema:
   - `GET /api/accounts/me/`
   - `GET /api/accounts/my-children/`
   - `POST /api/accounts/parent-links/`
   - `GET /api/accounts/organizations/{organization_pk}/children/`
   - Teacher configurations
   - `GET /api/organizations/mine/`
   - `POST /api/organizations/`
   - `GET /api/organizations/{organization_pk}/`
   - `GET/PATCH /api/organizations/{organization_pk}/memberships/`
   - `GET/POST/PATCH /api/organizations/{organization_pk}/students/`
   - `GET/POST /api/organizations/{organization_pk}/invitations/`
   - `POST /api/organizations/{organization_pk}/invitations/accept/`
   - organization-scoped scheduling, pricing, assessment, payout, notification, import, audit endpoints
3. Ensure student list/detail and invitation responses expose structured schemas, not just raw descriptions.
4. Run `python manage.py check` and verify `schema.yml`.


