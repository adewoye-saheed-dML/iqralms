# Task 5.1 — Pricing Ownership Audit

Inspect `PricingAgreement`, student membership, `Level`, `Track`, `Organization`, pricing serializers/views/permissions, URLs, OpenAPI configuration, services/helpers, and tests.

Document:

1. How an agreement currently resolves to an academy.
2. Whether ownership is direct or derived.
3. Whether cross-academy student/level combinations are possible.
4. Who can create, approve, view, update, deactivate, and list agreements.
5. Which routes are globally scoped.
6. Existing pricing test coverage.
7. Minimum changes required for tenant safety.

Prefer `PricingAgreement -> Level -> Track -> Organization` and avoid redundant tenant fields unless the audit proves they are necessary.

---

## Audit Findings & Report

### 1. How an Agreement Currently Resolves to an Academy
* **Current state**: `PricingAgreement` has foreign keys to `student` (`User`), `level` (`Level`), and `approved_by` (`User`).
* There is **no** `organization` foreign key or property on `PricingAgreement`.
* `Level` belongs to `Track` via `level.track`, and `Track` belongs to `Organization` via `track.organization`.
* Therefore, an agreement can resolve to an academy only indirectly via:
  ```text
  PricingAgreement -> Level -> Track -> Organization
  ```
* In existing code, no model methods, serializers, views, or permissions inspect `level.track.organization`. The pricing domain currently operates completely globally.

### 2. Whether Ownership is Direct or Derived
* **Ownership is derived**:
  ```text
  PricingAgreement
      -> Level (on_delete=PROTECT)
          -> Track (on_delete=CASCADE)
              -> Organization (on_delete=CASCADE)
  ```
* **Redundant tenant field evaluation**:
  - Every agreement is strictly bound to a single mandatory `Level`.
  - Every `Level` is strictly bound to a single mandatory `Track`.
  - Every `Track` is strictly bound to a single mandatory `Organization`.
  - Adding a direct `organization` foreign key on `PricingAgreement` would create data redundancy and the risk of synchronization divergence (`agreement.organization != agreement.level.track.organization`).
  - Read-only property `@property def organization(self): return self.level.track.organization if self.level_id else None` provides convenient object-level access.
  - Queryset helper `PricingAgreementQuerySet.in_organization(self, organization)` filtering by `level__track__organization_id` provides index-backed tenant filtering.
  - **Conclusion**: The audit proves that derived ownership is completely sufficient. No direct `organization` column is needed or recommended.

### 3. Whether Cross-Academy Student/Level Combinations are Possible
* **Current state**: **YES**, cross-academy combinations are completely unrestricted.
* `PricingAgreement.clean()` only validates:
  - `self.student.role == Role.STUDENT`
  - `self.approved_by.role == Role.LEAD`
* It does **not** verify:
  - That `student` has an active membership in `level.track.organization`.
  - That `approved_by` has an active membership in `level.track.organization`.
* In `PricingAgreementCreateSerializer`:
  - `student = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role=Role.STUDENT))` (global!)
  - `level = serializers.PrimaryKeyRelatedField(queryset=Level.objects.all())` (global!)
* A lead from Academy A can currently create an agreement pairing a student from Academy B with a curriculum level from Academy C, approved by a lead from Academy A.
* **Target rule**:
  - `student` must be an active member of `level.track.organization`.
  - `approved_by` must be an active member of `level.track.organization` and hold `role == Role.LEAD`.
  - Cross-academy combinations must fail validation at both the model layer and serializer layer.

### 4. Who Can Create, Approve, View, Update, Deactivate, and List Agreements
* **Create**:
  - Currently: Any authenticated user with global `role == Role.LEAD` can POST to `/api/pricing/agreements/`. Approver is stamped from `request.user`.
  - Tenant safe: Only an active member with lead authority in the target academy can create agreements for that academy.
* **Approve**:
  - Currently: Any user with global `role == Role.LEAD`.
  - Tenant safe: The approver must have an active membership in `level.track.organization` with `role == Role.LEAD`.
* **View / List (Lead)**:
  - Currently: Any global `Role.LEAD` can call `GET /api/pricing/agreements/?student_id=<id>` and view full pricing history for ANY student across all academies, including private notes.
  - Tenant safe: Must require active membership in the organization (`IsOrganizationMember`) and lead role (`IsLeadTeacher`). Queryset scoped strictly to `in_organization(organization)`. Student must be an active member of that organization.
* **View / List (Student `/mine/`)**:
  - Currently: Any global `Role.STUDENT` can call `GET /api/pricing/agreements/mine/` and retrieve active agreements across all academies globally.
  - Tenant safe: Scoped to `/api/pricing/organizations/<organization_pk>/agreements/mine/`. Student must be an active member of that organization. Returns only active agreements for that student in that organization.
* **Update**:
  - Currently: No API endpoint allows updating agreements. Pricing agreements are immutable and historical records are preserved by superseding. Admin allows editing `active`.
  - Tenant safe: Preserve immutable superseding design. Model `clean()` validates tenant integrity for admin/ORM writes.
* **Deactivate**:
  - Currently: Automatically handled via `supersede_active()` in `save()`, which deactivates prior active agreements for `(student, level)`.
  - Tenant safe: Because `level` uniquely belongs to one academy, `(student, level)` superseding naturally operates within that single academy.
* **Privacy**:
  - `notes` and `approved_by` remain strictly private to leads and are never exposed to students/families.

### 5. Which Routes are Globally Scoped
* **Current legacy global routes** (`pricing/urls.py`):
  - `GET  /api/pricing/agreements/mine/`
  - `GET  /api/pricing/agreements/?student_id=<id>`
  - `POST /api/pricing/agreements/`
* **Target organization-scoped routes**:
  - `GET  /api/pricing/organizations/<organization_pk>/agreements/mine/`
  - `GET  /api/pricing/organizations/<organization_pk>/agreements/?student_id=<id>`
  - `POST /api/pricing/organizations/<organization_pk>/agreements/`
* Legacy routes must be retired and blocked so they cannot be used as an isolation bypass.

### 6. Existing Pricing Test Coverage
* Current test suite consists of **61 tests** (all passing in 12.2s on PostgreSQL):
  - `pricing/tests/test_models.py`: 18 tests (creation, standard rate snapshots, discounts/premiums, zero rates, negative rate refusal, per-level agreements, superseding logic, partial unique constraint, approver role gates, student role gates, isolation from teacher payouts, no booking price field).
  - `pricing/tests/test_api.py`: 43 tests (lead creation, approver stamping, inactive agreement prevention, notes optionality, standard rate agreement, reason display, invalid inputs, permissions for sub-teachers/students/parents/anonymous, HTTP superseding, lead history viewing with private notes, student `/mine/` endpoint privacy and filtering).
* **Test gaps identified**:
  - Zero multi-academy tenant isolation tests exist for pricing.
  - Zero tests verifying that an Academy A lead cannot list, view, or create agreements in Academy B.
  - Zero tests verifying that cross-tenant student and level pairing is rejected.
  - Zero tests verifying that `/mine/` in Academy B excludes Academy A agreements.
  - Current factories (`PricingAgreementFactory`) create students and levels without organization memberships.

### 7. Minimum Changes Required for Tenant Safety
1. **Model layer (`pricing/models.py`)**:
   - Add `organization` property on `PricingAgreement` resolving to `self.level.track.organization`.
   - Implement `PricingAgreementQuerySet.in_organization(organization)` and attach to `PricingAgreement.objects`.
   - Update `PricingAgreement.clean()`:
     - Enforce `active_membership(user=self.student, organization=self.organization) is not None`.
     - Enforce `active_membership(user=self.approved_by, organization=self.organization) is not None`.
2. **Factories (`pricing/tests/factories.py`)**:
   - Update `PricingAgreementFactory._create()` to automatically provision active organization memberships for `student` and `approved_by` in `level.track.organization`.
3. **Serializers (`pricing/serializers.py`)**:
   - Narrow `level` queryset to `levels_in(self.context["organization"])`.
   - Narrow `student` queryset to active student members of `self.context["organization"]`.
   - Default both field querysets to `.none()` to fail closed when no context is provided.
4. **Views (`pricing/views.py`)**:
   - Inherit from `AcademyScopedView(OrganizationScopedMixin)`.
   - Enforce `IsAuthenticated` and `IsOrganizationMember`.
   - Scope querysets to `.in_organization(self.organization)`.
   - Add `organization` into serializer context.
   - For lead history GET: validate student is an active member in the academy.
   - For student `/mine/`: filter by active membership and organization.
5. **URLs (`pricing/urls.py`)**:
   - Mount routes under `/api/pricing/organizations/<organization_pk>/agreements/`.
   - Retire legacy unscoped endpoints.
6. **Test Suites**:
   - Update existing unit and API tests to supply organization context.
   - Add comprehensive adversarial tenant isolation tests in `pricing/tests/test_tenant_isolation.py`.
7. **Legacy Data Policy & Migrations (`pricing/legacy.py`)**:
   - Implement data migration / legacy verification ensuring any historical agreement rows have valid tenant memberships.

