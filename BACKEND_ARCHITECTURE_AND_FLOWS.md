# IqraLMS Backend Architecture & Execution Flows

> **Definitive Single Source of Truth (SSoT) for IqraLMS Django Backend**  
> *Consolidates and supersedes all prior phase notes, remediation documents, and temporary migration records.*

---

## 1. Architectural Overview & Core Principles

IqraLMS is a multi-tenant, cloud-native Quranic Learning Management System built with **Python 3.14**, **Django 6**, and **Django REST Framework (DRF)**.

```
                           +---------------------------+
                           |  Next.js 16 Frontend App  |
                           +-------------+-------------+
                                         |  Bearer Auth (Token) / HMAC Webhooks
                                         v
                      +-------------------------------------+
                      |   Django REST API (/api/...)        |
                      +-------------------------------------+
                      | Tenancy Middleware & Permission Map |
                      +-------------------------------------+
                                         |
     +---------------+-------------------+---------------+-------------------+
     |               |                   |               |                   |
     v               v                   v               v                   v
[organizations] [accounts]         [curriculum]    [scheduling]        [assessment]
 - Tenants       - CustomLogin      - Tracks        - Teacher Avail.    - Homework
 - Memberships   - Roles            - Levels        - Bookings          - Audio Streams
 - Roles         - Family Linking   - Materials     - Sub-Routing       - Rubrics
                                                                             |
     +---------------+-------------------+---------------+                   |
     |               |                   |               |                   |
     v               v                   v               v                   v
 [billing]       [payments]          [payouts]     [notifications]      [audit_logs]
 - Tiers (Caps)  - Paystack Webhooks - Rates       - In-app / Push      - Immutable logs
 - Geo-Currency  - Splits & Tuitions - Settlement  - Email Templates    - Actor hashing
```

### Architectural Guarantees:
1. **Strict Multi-Tenant Isolation**: Every operational entity (students, tracks, levels, bookings, assessments, payouts) is strictly scoped to an `organization_pk`. Cross-tenant queries are blocked at both database and view querysets (`get_queryset()` overrides).
2. **Canonical PostgreSQL Database**: PostgreSQL is strictly enforced via `DATABASE_URL`. There is no SQLite fallback in production or testing.
3. **OpenAPI / REST SSoT**: Serializers generate OpenAPI 3.0 schemas via `drf-spectacular` (`schema.yml`), which generate TypeScript contracts in the frontend (`openapi/schema.yml` -> `src/lib/api/schema.d.ts`).
4. **Hardened Webhook Ingestion**: Webhooks (specifically Paystack payment events) require HMAC-SHA512 header validation (`X-Paystack-Signature`). Bypass loopholes in debug mode are strictly prohibited.
5. **Rate Limiting**: Automated IP and user-level throttling protects public and authenticated endpoints (`DEFAULT_THROTTLE_CLASSES`).

---

## 2. Core Domain Models & Relationships

### A. Organizations & Identity (`organizations`, `accounts`)
- **`Organization`**: The tenant boundary (Academy). Contains name, slug, timezone, primary currency (`NGN`, `USD`, etc.), and Paystack subaccount codes.
- **`User`**: Custom user model (`accounts.User`). Identified by `username` and `email`. Users can be members of multiple academies through `OrganizationMembership`.
- **`Role`**:
  - `OWNER`: Full administrative authority over academy, settings, billing, payouts, curriculum, and staff.
  - `ADMIN`: Operational administrator (scheduling, curriculum management, staff allocation).
  - `TEACHER` / `LEAD`: Conducts classes, creates homework, grades submissions, reviews rubrics.
  - `STUDENT`: Attends sessions, submits recitation recordings, views learning space.
  - `PARENT`: Manages linked children (`ParentChildLink`), initiates tuition payments, monitors ward progress.
- **`StudentEnrollment`**: Links a `User` (student) to an `Organization`. Supports multi-track enrollments (a student can be enrolled in Quran Memorization, Tajweed, and Islamic Studies concurrently).

### B. Curriculum & Materials (`curriculum`)
- **`Track`**: Subject track (e.g. *Hifz Memorization*, *Nazira*, *Tajweed Rules*).
- **`Level`**: Milestone stage within a track (e.g. *Level 1 - Al-Fatihah & Juz Amma*), ordered sequentially (`order`).
- **`LearningMaterial`**: Learning assets (PDF worksheets, audio guides, books, recitation links). Supports private file uploads or external URLs.
- **`PlacementResult`**: Initial level evaluation result for a student on a specific track, verified by lead teachers.

### C. Scheduling & Classroom (`scheduling`)
- **`TeacherAvailabilityWindow`**: Recurring weekly slots (0 = Monday, ..., 6 = Sunday) in UTC, translated to the teacher's local timezone.
- **`Booking`**: A scheduled 1-on-1 or group session linking a student, teacher, level, and track.
- **`SubRouting`**: If the primary teacher is unavailable, requests route to available substitute teachers specializing in the track based on weekly capacity balance.
- **`WaitlistEntry`**: Queues student session requests when preferred teachers or slots are fully booked.

### D. Assessment & Learning Space (`assessment`)
- **`StudentAssignment`**: Continuous homework assigned to a track, level, or individual student. Supports Quran recitation parameters (`surah_number`, `ayah_start`, `ayah_end`), file attachments, and due dates.
- **`AssignmentSubmission`**: Student submission containing audio recitation recordings (`audio_recording`), written responses, or attachments.
- **`AssessmentRubric` & `RubricCriterion`**: Evaluation criteria with maximum weights (e.g. *Tajweed Accuracy: 40*, *Makharij: 30*, *Fluency: 30*).
- **`SessionAssessment`**: Teacher feedback and scores recorded immediately after a live lesson.

### E. Billing, Payments & Payouts (`billing`, `payments`, `payouts`)
- **`PlatformSubscription`**: Academy SaaS plan (`starter`, `growth`, `professional`, `institutional`). Enforces active student caps (`can_add_student`).
- **`FamilyPayment` / `PaymentAgreement`**: Tuition agreements between parents and academies. Supports automatic geo-currency routing (NGN for Nigeria, USD for diaspora).
- **`TeacherPayout`**: Monthly or periodic settlement batches generated based on completed sessions and compensation agreements (hourly, fixed monthly, or per-student).

---

## 3. End-to-End Execution Flows

```
[Flow 1: Academy Setup]
Creator -> /api/auth/register/ -> User -> /api/organizations/ -> Academy created
                                                        |
                                                        v
                                            PlatformSubscription (Starter/Trial)

[Flow 2: Student Allocation]
Parent/Student -> /api/organizations/{pk}/students/ -> StudentEnrollment
                                                                |
                                                                v
                                              Track Enrollment -> Level Assigned

[Flow 3: Session Lifecycle]
Teacher declares /availability/ -> Parent Books /bookings/ -> Live Classroom
                                                                     |
                                                                     v
                                                          SessionAssessment recorded

[Flow 4: Continuous Homework]
Teacher creates Assignment -> Student records audio -> Teacher grades via Rubric
                                                                     |
                                                                     v
                                              Disclosed in /ward-progress/ space

[Flow 5: Billing & Payouts]
Parent pays tuition -> Paystack Webhook verified -> Subaccount split settled
                                                                |
                                                                v
                                               Teacher Payout Batch finalized
```

---

## 4. API Endpoints Map (Backend SSoT)

| App | Base Route | View Classes | Roles / Permissions |
|---|---|---|---|
| **Auth** | `/api/auth/` | `CustomLoginView`, `RegisterView`, `MeView`, `PasswordResetView` | Public / Token Authenticated |
| **Organizations** | `/api/organizations/` | `OrganizationViewSet`, `StudentEnrollmentView` | Owner, Admin, Members |
| **Curriculum** | `/api/curriculum/organizations/{pk}/` | `TrackView`, `LevelView`, `LearningMaterialView`, `PlacementView` | Teacher, Student, Parent |
| **Scheduling** | `/api/scheduling/organizations/{pk}/` | `TeacherAvailabilityView`, `BookingViewSet`, `WaitlistView` | Teacher, Parent, Student |
| **Assessment** | `/api/assessment/organizations/{pk}/` | `AssignmentView`, `SubmissionView`, `RubricView`, `WardProgressView` | Teacher, Lead, Student, Parent |
| **Billing** | `/api/billing/` | `PlatformSubscriptionView`, `TierListView`, `BillingLocationView` | Academy Owner |
| **Payments** | `/api/payments/` | `TuitionPaymentView`, `UnifiedPaystackWebhookView` | Parent, Paystack Webhooks |
| **Payouts** | `/api/payouts/organizations/{pk}/` | `PayoutGenerateView`, `TeacherPayoutView`, `BulkFinalizeView` | Owner, Teacher |

---

## 5. Production Readiness & Deployment Checklist

1. **Environment Variables**:
   - `DATABASE_URL`: Required PostgreSQL connection string (e.g. `postgres://user:pass@host:5432/iqralms`).
   - `SECRET_KEY`: High-entropy production key.
   - `DEBUG`: Must be `False` in production.
   - `ALLOWED_HOSTS`: Explicit domain list (e.g. `api.iqralms.com`).
   - `CORS_ALLOWED_ORIGINS`: Frontend URL list (e.g. `https://app.iqralms.com`).
   - `PAYSTACK_SECRET_KEY`: Live Paystack secret key starting with `sk_live_`.
   - `PAYSTACK_WEBHOOK_SECRET`: Secret key used to verify `X-Paystack-Signature`.
   - `DJANGO_STORAGE_BACKEND`: `s3-private` (S3/Cloudflare R2) in production, `private-local` in development.
2. **Database Migrations**:
   - Run `python manage.py migrate --noinput`.
3. **Deployment Check**:
   - Run `python manage.py check --deploy`.
4. **Process Execution**:
   - WSGI/ASGI: Run with Gunicorn or Daphne (`daphne -b 0.0.0.0 -p 8000 config.asgi:application`).

---

## 6. Troubleshooting & Diagnostics ("When Things Break")

### A. Paystack Webhook Not Activating Subscription or Tuition
- **Symptom**: Parent or academy paid on Paystack, but status remains `pending` or features remain locked.
- **Where to check**: [`payments/views.py`](file:///home/iscsi/projects/iqralms/payments/views.py) -> `UnifiedPaystackWebhookView`.
- **Diagnostic Steps**:
  1. Inspect Nginx/server logs for `POST /api/payments/webhook/paystack/`.
  2. Verify HTTP status:
     - `400 Bad Request`: `X-Paystack-Signature` header was missing or HMAC signature did not match `PAYSTACK_SECRET_KEY`.
     - `200 OK`: Webhook was received. Check database: `PlatformSubscription.objects.get(organization_id=...)` or `FamilyPayment.objects.get(reference=...)`.
  3. Ensure the Paystack Dashboard webhook URL points to `/api/payments/webhook/paystack/`.

### B. Student Cannot See Learning Materials or Homework
- **Symptom**: Student dashboard shows empty state for curriculum materials or homework.
- **Where to check**:
  - [`curriculum/views.py`](file:///home/iscsi/projects/iqralms/curriculum/views.py) -> `AcademyLearningMaterialListView`.
  - [`assessment/views.py`](file:///home/iscsi/projects/iqralms/assessment/views.py) -> `AssignmentListView`, `StudentWardAssessmentsView`.
- **Diagnostic Steps**:
  1. Check if the student has an active enrollment: `StudentEnrollment.objects.filter(user=student, organization=org)`.
  2. If the material/assignment is level-scoped, verify `enrollment.level_id` matches `material.level_id`.
  3. If assigned to an individual student, check `assignment.assigned_student_id == student.id`.

### C. Teacher Cannot Grade or Edit Assignment
- **Symptom**: HTTP 403 Forbidden when teacher tries to edit assignment or grade submission.
- **Where to check**: [`assessment/views.py`](file:///home/iscsi/projects/iqralms/assessment/views.py) -> `AssignmentDetailView`, `SubmissionGradeView`.
- **Diagnostic Steps**:
  1. Teachers can only edit/delete assignments they created, unless they are `lead` or academy `owner`/`admin`.
  2. Verify: `assignment.created_by == request.user` or `request.user.role in ['lead', 'owner', 'admin']`.

### D. Booking Cancellation Fails with "No Booking matches given query"
- **Symptom**: Cancelling a session fails with 404 or empty query.
- **Where to check**: [`scheduling/views.py`](file:///home/iscsi/projects/iqralms/scheduling/views.py) -> `BookingDetailView`.
- **Diagnostic Steps**:
  1. Check tenant scoping: URL requires `organizations/{organization_pk}/bookings/{id}/`.
  2. Verify that the booking belongs to that exact organization: `Booking.objects.filter(id=id, level__track__organization_id=organization_pk)`.

### E. Rate Limit Triggered (HTTP 429 Too Many Requests)
- **Symptom**: User receives HTTP 429 errors during intensive actions.
- **Where to check**: [`config/settings.py`](file:///home/iscsi/projects/iqralms/config/settings.py) -> `REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']`.
- **Diagnostic Steps**:
  1. Anons are throttled at 100/min; authenticated users at 1000/min.
  2. For webhook routes or trusted internal callers, ensure view sets `throttle_classes = []`.
