# Task 6.1 — Assessment Ownership and Data-Flow Audit

## Status

COMPLETE

## Objective

Map every assessment model, relationship, queryset, serializer, permission, endpoint, report, and administrative path to its academy owner.

## Audit targets

Inspected:

- `assessment/models.py`
- `assessment/serializers.py`
- `assessment/views.py`
- `assessment/permissions.py`
- `assessment/reporting.py`
- `assessment/urls.py`
- assessment migrations
- assessment tests
- curriculum and accounts tenancy helpers
- parent-child access paths
- admin and service-layer writes
- OpenAPI route registration

---

## Audit Findings & Report

### 1. Organization Owner for Each Assessment Model
- **`AssessmentRubric`**: Owned by `rubric.track.organization`.
- **`AssessmentCriterion`**: Owned by `criterion.rubric.track.organization`.
- **`SessionAssessment`**: Owned by `assessment.booking.level.track.organization` (and `assessment.track.organization`, which are invariant-equal).
- **`AssessmentScore`**: Owned by `score.assessment.track.organization`.
- **`ProgressSnapshot`**: Owned by `snapshot.track.organization`.

### 2. Whether Ownership is Direct or Derived
- **Ownership is strictly derived**:
  ```text
  AssessmentRubric
      -> Track (on_delete=CASCADE)
          -> Organization (on_delete=CASCADE)

  AssessmentCriterion
      -> AssessmentRubric (on_delete=CASCADE)
          -> Track -> Organization

  SessionAssessment
      -> Booking (on_delete=PROTECT)
          -> Level -> Track -> Organization
      (and -> Track -> Organization)

  AssessmentScore
      -> SessionAssessment (on_delete=CASCADE)
          -> Track -> Organization

  ProgressSnapshot
      -> Track (on_delete=PROTECT)
          -> Organization (on_delete=CASCADE)
  ```
- **Evaluation of redundant tenant columns**:
  - `Track` has non-nullable `organization = ForeignKey(Organization, on_delete=CASCADE)`.
  - `Booking` has non-nullable `level` (`PROTECT`) which points to `track` (`CASCADE`) which points to `organization`.
  - Adding a redundant `organization_id` foreign key to `AssessmentRubric`, `SessionAssessment`, or `ProgressSnapshot` would create denormalization risk and potential data divergence (e.g. `rubric.organization_id != rubric.track.organization_id`).
  - Read-only property `@property def organization(self)` and querysets filtering on `.in_organization(organization)` provide clean, unambiguous tenant scoping, consistent with decisions made in SaaS Phase 3 (`Level`), Phase 4 (`Booking`), and Phase 5 (`PricingAgreement`).
  - **Conclusion**: Derived ownership is completely sufficient and preferable. No schema change / direct `organization` column is needed.

### 3. How a Student is Linked to the Organization
- A student (`User` with `role == Role.STUDENT`) is linked to an academy via `OrganizationMembership(user=student, organization=org, status=MembershipStatus.ACTIVE)`.
- Verified through `accounts.tenancy.active_student_membership(user=student, organization=org)`.
- Currently, neither `SessionAssessment.clean()` nor `ProgressSnapshot.clean()` validates that `student` is an active member in `self.organization`.
- **Target invariant**: In `SessionAssessment.clean()` and `ProgressSnapshot.clean()`, require `active_student_membership(user=self.student, organization=self.organization)` to be present.

### 4. How a Teacher is Authorized within the Organization
- An assessing teacher (`User` with `role in TEACHER_ROLES`) must be:
  1. An active member of the academy: `active_membership(user=teacher, organization=org)`.
  2. Approved to teach in that academy: `OrganizationTeacherConfiguration.objects.filter(membership=m, approved=True).exists()`.
  3. Qualified in that track: `TeacherTrack.objects.filter(membership=m, track=track, active=True).exists()`.
- Currently, `SessionAssessment._validate_assessor` calls `bookable_teacher_error(self.assessed_by)` without passing `organization`. When a teacher has memberships in multiple academies, this raises `multiple_organizations_ambiguous`.
- **Target invariant**: Pass `organization=self.organization` to `bookable_teacher_error(self.assessed_by, organization=self.organization)` in `_validate_assessor`.
- For lead reviews (`lead_reviewed_by`): Currently `_validate_lead_review` only checks `role == Role.LEAD`. It must verify `active_membership(user=self.lead_reviewed_by, organization=self.organization)` with lead authority.

### 5. How a Parent Reaches a Child's Assessment Records
- Access must go through `accounts.tenancy.children_in_organization(parent=parent, organization=org)`.
- Requires:
  1. A `ParentLink(parent=parent, student=student)`.
  2. The parent must have an active membership in `org`.
  3. The child must have an active membership in `org`.
- Currently, `assessment/views.py::linked_child` checks only `ParentLink` globally without verifying organization membership!
- **Target invariant**: Replace global `linked_child` with tenant-aware verification using `children_in_organization(parent=request.user, organization=self.organization)`.

### 6. Existing Unscoped Queries and Object Lookups
- `assessment/urls.py` mounts all routes globally at `/api/assessment/...` instead of under `/api/assessment/organizations/<organization_pk>/...`.
- `AssessmentRubricListCreateView`: `AssessmentRubric.objects.all()` lists across all academies.
- `AssessmentRubricDetailView`: `AssessmentRubric.objects.all()` allows retrieving or patching any rubric across academies.
- `SessionAssessmentCreateView`: `Booking.objects.filter(...)` does not scope lookup to the organization.
- `MyAssessmentListView`: `SessionAssessment.objects.filter(student=request.user)` lists assessments globally across all academies.
- `MyChildAssessmentListView`: Uses global `linked_child` and global assessment filtering.
- `TeacherAssessmentListView`: Scoped only to `assessed_by=request.user`, mixing submissions across all academies.
- `LeadAssessmentDetailView`: `SessionAssessment.objects.all()` allows retrieving any assessment across academies by numeric ID.
- `LeadReviewQueueView`: `SessionAssessment.pending_lead_review()` lists flagged assessments across all academies.
- `LeadReviewView`: `SessionAssessment.objects.all()` allows reviewing assessments of any academy.
- `TeacherQualityReportView`: `teacher_report()` aggregates sessions across all academies when `track` is omitted.
- `MyProgressView` & `ChildProgressView`: `requested_track` queries `Track.objects.all()` globally.
- `ProgressSnapshotCreateView`: `ProgressSnapshotCreateSerializer` exposes global querysets for `student` and `track`.
- `LeadSnapshotListView`: `ProgressSnapshot.objects.all()` lists snapshots across all academies.
- `MySnapshotListView` & `ChildSnapshotListView`: Lists snapshots across all academies without tenant filtering.

### 7. Existing Cross-Tenant Validation Gaps
- `AssessmentRubric`: Can be created referencing a `track` belonging to another academy.
- `SessionAssessment`:
  - `clean()` does not pass `organization` to `bookable_teacher_error()`.
  - `clean()` does not check that `student` is an active member in `self.organization`.
  - `clean()` does not check that `lead_reviewed_by` is an active member with lead authority in `self.organization`.
  - `clean()` does not check that `booking.level.track.organization == self.organization`.
- `ProgressSnapshot`:
  - `clean()` does not verify that `student` is an active member of `track.organization`.
  - `clean()` does not verify that `generated_by` is an active member with lead authority in `track.organization`.
- Serializers:
  - `AssessmentRubricCreateSerializer`: `track` field uses `Track.objects.all()`.
  - `ProgressSnapshotCreateSerializer`: `student` uses `User.objects.all()` and `track` uses `Track.objects.all()`.

### 8. Legacy Rows and Remediation
- Existing database inspection revealed **0 rubrics, 0 criteria, 0 assessments, 0 scores, and 0 snapshots**.
- Any historical assessment rows that might exist in other environments are strictly anchored to `Track` and `Booking.level.track`. Both `Track` and `Booking` have non-nullable organization ownership established in SaaS Phases 3 and 4.
- Consequently, all historical assessment records have unambiguous, deterministic organization ownership.
- Zero orphaned assessment records can exist.

### 9. Required Changes by Component
1. **Model Layer (`assessment/models.py`)**:
   - Add `@property def organization(self)` to `AssessmentRubric`, `SessionAssessment`, and `ProgressSnapshot`.
   - Add custom QuerySets (`in_organization(org)`) for `AssessmentRubric`, `SessionAssessment`, and `ProgressSnapshot`.
   - Update `SessionAssessment._validate_assessor` to pass `organization=self.organization` to `bookable_teacher_error()`.
   - Update `SessionAssessment.clean()` to validate active memberships for `student`, `assessed_by`, and `lead_reviewed_by`.
   - Update `ProgressSnapshot.clean()` to validate active memberships for `student` and `generated_by`.
   - Update `ProgressSnapshot.generate()` to ensure all aggregation queries are scoped to the organization.
2. **Reporting (`assessment/reporting.py`)**:
   - Update `teacher_report` to accept `organization` and scope `SessionAssessment` queries to `in_organization(organization)`.
   - Update `student_progress` to verify that `track.organization == organization`.
3. **Serializers (`assessment/serializers.py`)**:
   - Implement `AcademyScopedSerializerMixin` / inherit `AcademyScopedSerializer`.
   - In `AssessmentRubricCreateSerializer`: narrow `track` to `tracks_in(self.organization)` and default to `.none()`.
   - In `ProgressSnapshotCreateSerializer`: narrow `track` to `tracks_in(self.organization)` and `student` to active student members of `self.organization`, defaulting to `.none()`.
   - In `SessionAssessmentCreateSerializer`: assert `booking.organization == self.organization`.
4. **Views (`assessment/views.py`)**:
   - Implement `AcademyScopedView(OrganizationScopedMixin)` as base for all assessment views.
   - Enforce `IsAuthenticated` and `IsOrganizationMember`.
   - Narrow all querysets to `.in_organization(self.organization)`.
   - Update parent access to use `accounts.tenancy.children_in_organization(parent=request.user, organization=self.organization)`.
   - Update `requested_track` to only resolve tracks in `self.organization`.
5. **URLs (`assessment/urls.py`)**:
   - Prefix all routes with `organizations/<int:organization_pk>/`.
   - Retire/block legacy unscoped routes.
6. **Factories & Tests (`assessment/tests/`)**:
   - Update `assessment/tests/factories.py` to ensure consistent tenant provisioning across `Level`, `Availability`, `Booking`, `Teacher`, and `Student`.
   - Create comprehensive adversarial tenant isolation tests in `assessment/tests/test_tenant_isolation.py`.
