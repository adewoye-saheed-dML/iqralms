# SaaS Phase 3 — Curriculum Tenancy

## Goal

Make the existing `curriculum` domain organization-aware without breaking the accepted original curriculum behaviour or the accepted SaaS Phase 1 and Phase 2 account model.

This phase establishes the academy boundary for:

```text
Track
Level
PlacementResult
Teacher curriculum eligibility
```

The outcome is:

> Each academy owns its curriculum and placement data, while a global user may participate in multiple academies without curriculum data leaking across tenants.

This phase prepares the curriculum domain for SaaS scheduling tenancy. It must not migrate scheduling, pricing, assessment, or payouts yet.

---

# 1. Current Architecture

The curriculum domain currently contains:

```text
Track
Level
PlacementResult
```

Current global relationships include:

```text
Track
  └── Level

User
  └── PlacementResult ──> Track
                         └── recommended Level
```

The account domain now contains:

```text
User
OrganizationMembership
OrganizationTeacherConfiguration
TeacherProfile
```

The legacy teacher specialty relationship remains:

```text
TeacherProfile.specialties -> curriculum.Track
```

That relationship is the remaining curriculum tenancy gap identified in SaaS Phase 2.

---

# 2. Phase Responsibilities

This phase owns:

- organization ownership of curriculum;
- organization-scoped Track and Level data;
- organization-scoped PlacementResult data;
- organization-safe placement review;
- organization-safe private placement audio access;
- academy-scoped teacher curriculum eligibility;
- safe migration of legacy global specialties;
- curriculum tenant-isolation tests;
- preservation of original curriculum behaviour.

This phase does not own:

- scheduling tenancy;
- booking tenancy;
- availability tenancy;
- routing tenancy;
- pricing tenancy;
- assessment tenancy;
- payout tenancy;
- onboarding;
- invitations;
- notifications;
- billing;
- frontend.

---

# 3. Target Architecture

Preferred structure:

```text
Organization
    |
    +-- Track
          |
          +-- Level

OrganizationMembership
    |
    +-- TeacherTrack
             |
             +-- Track

Track
    |
    +-- PlacementResult
```

A curriculum object must have one unambiguous owning academy.

At the end of the phase:

```text
Track.organization != null
```

for every stored track.

Do not permanently leave organization ownership optional.

Do not create one database per academy.

Do not create a second global curriculum system that shares mutable tenant records.

---

# 4. Track

The existing Track fields remain:

```text
organization
name
slug
```

Add organization ownership without changing the meaning of:

```text
name
slug
```

## Uniqueness

The old global unique slug is no longer correct.

Use:

```text
Unique(organization, slug)
```

Therefore:

```text
Academy A -> tajweed
Academy B -> tajweed
```

is valid.

But:

```text
Academy A -> tajweed
Academy A -> tajweed
```

is rejected.

Do not retain global slug uniqueness.

## Ownership

A track is owned by exactly one academy.

Changing the organization of an existing track is not a normal CRUD operation because it moves all levels and placements with it.

Prefer immutable ownership after creation.

If an academy-transfer workflow is proposed, stop and record a separate product decision before implementing it.

---

# 5. Level

Keep the existing fields:

```text
track
order
name
min_age
group_eligible
```

A Level belongs to an academy through its Track.

Invariant:

```text
Level.track.organization
```

is the Level's academy.

Do not duplicate `organization` on Level unless a concrete integrity/performance requirement proves it necessary.

Preserve the original Phase 2 ordering rules:

- first level is `order=1`;
- new levels append to `max(order)+1`;
- duplicate order is rejected;
- existing order cannot be changed through ordinary editing;
- `min_age` remains informational;
- `group_eligible` remains intact.

Existing ordering tests must remain green.

---

# 6. PlacementResult

Keep the existing fields:

```text
student
track
audio_sample
skipped_as_beginner
recommended_level
reviewed_by
reviewed_at
status
```

Placement ownership is derived through the academy-owned Track.

Required relationship:

```text
PlacementResult.track.organization
```

must define the placement's academy.

The existing level invariant remains mandatory:

```text
recommended_level.track_id == track_id
```

Reject any attempt to combine records from different academies.

Example that must fail:

```text
Academy A student
Academy A track
Academy B recommended level
```

---

# 7. Student Membership

A global student account is not automatically a member of every academy.

For academy-specific placement operations:

```text
student.role == student
AND
active OrganizationMembership exists for the target academy
```

must both be true.

Therefore:

```text
Student S ∈ Academy A
Student S ∉ Academy B
```

means:

```text
placement in A -> may be accessed
placement in B -> must be denied
```

even when the placement or track id is known.

A membership that is suspended must not grant curriculum access.

---

# 8. Parent Access

Keep `ParentLink` global.

Do not make ParentLink academy-owned in Phase 3.

For academy-specific placement access by a parent:

```text
parent active in organization
AND
student active in organization
AND
ParentLink(parent, student)
```

must all hold.

Reuse:

```text
accounts.tenancy.children_in_organization()
```

Do not duplicate this rule in curriculum code.

A global ParentLink is not authorization to another academy's data.

---

# 9. Teacher Curriculum Eligibility

The legacy structure is:

```text
TeacherProfile.specialties
        |
        v
Global Track
```

This does not represent a teacher working for multiple academies with different teaching responsibilities.

Introduce an academy-scoped relationship:

```text
OrganizationMembership
        |
        +-- TeacherTrack
                |
                +-- Track
```

Minimum semantics:

```text
organization membership
track
active
```

Optional extra fields may only be added when a current business requirement needs them.

The relationship must ensure:

```text
membership.organization == track.organization
```

A teacher may teach many tracks in an academy.

A track may have many teachers.

The same teacher may have different track assignments in different academies.

Example:

```text
Teacher T

Academy A
    Tajweed
    Hifz

Academy B
    Arabic
```

This is required behaviour.

---

# 10. TeacherProfile Compatibility

SaaS Phase 2 deliberately kept `TeacherProfile` unchanged because scheduling and payout code still consume it.

Do not delete or redesign it prematurely.

Phase 3 rules:

- new academy curriculum eligibility is represented by the new tenant-scoped teacher-track relation;
- the legacy `TeacherProfile.specialties` relation must not grant cross-academy curriculum access;
- the old relation may remain temporarily for scheduling compatibility;
- do not remove it until SaaS Phase 4 has a safe migration path;
- document which consumers still read it.

Do not make Phase 3 silently rewrite scheduling logic.

---

# 11. Legacy Specialty Migration

Existing global specialties must be handled deterministically.

Preferred approach:

1. identify the academy that owns the migrated existing curriculum;
2. assign existing Tracks to that academy;
3. preserve existing Track primary keys where practical;
4. migrate legacy teacher specialty assignments for that academy to the new teacher-track relation;
5. do not copy those assignments into unrelated academies;
6. keep the legacy field only as a compatibility layer until downstream scheduling migrates;
7. document the backfill in `learnings.md`.

If a legacy teacher specialty cannot be mapped to a determinable academy, stop and document the ambiguity.

Do not assign every teacher every track as a convenience.

---

# 12. API Context

New organization-scoped curriculum endpoints should follow:

```text
/api/curriculum/organizations/{organization_id}/...
```

Preferred resources:

```text
tracks
levels
placements
teachers
```

The URL organization is only input context. The server must verify active membership.

Do not accept an organization id in the request body as authorization proof.

Do not trust a request body containing:

```text
organization_id
track_id
student_id
```

merely because the values look consistent.

Verify every relationship server-side.

---

# 13. Track API

Preferred endpoints:

```text
GET    /api/curriculum/organizations/{organization_id}/tracks/
POST   /api/curriculum/organizations/{organization_id}/tracks/
GET    /api/curriculum/organizations/{organization_id}/tracks/{id}/
PATCH  /api/curriculum/organizations/{organization_id}/tracks/{id}/
```

The implementation may use a slightly different repository-consistent shape, but it must provide equivalent behaviour.

### Track listing

List only tracks owned by the requested academy.

Never return:

```text
Track.objects.all()
```

from an organization-scoped endpoint.

### Track creation

The created track must always belong to the organization represented by the verified route context.

Ignore conflicting client-supplied ownership fields.

### Track mutation

The target Track must belong to the route organization.

A valid Track id from another academy must return a safe denial, normally by queryset scoping to the organization.

---

# 14. Level API

Preferred endpoints:

```text
GET    /api/curriculum/organizations/{organization_id}/levels/
POST   /api/curriculum/organizations/{organization_id}/levels/
GET    /api/curriculum/organizations/{organization_id}/levels/{id}/
PATCH  /api/curriculum/organizations/{organization_id}/levels/{id}/
```

Nested forms such as:

```text
/api/curriculum/organizations/{organization_id}/tracks/{track_id}/levels/
```

are acceptable where they make ownership validation clearer.

A client must not create a Level under:

```text
Academy A route
+
Academy B track id
```

The server must reject the combination.

---

# 15. Placement API

Preferred endpoints:

```text
POST   /api/curriculum/organizations/{organization_id}/placements/
GET    /api/curriculum/organizations/{organization_id}/placements/mine/
GET    /api/curriculum/organizations/{organization_id}/placements/pending/
POST   /api/curriculum/organizations/{organization_id}/placements/{id}/review/
GET    /api/curriculum/organizations/{organization_id}/placements/{id}/audio-url/
```

The exact route names may follow existing repository conventions.

The following original behaviours remain unchanged:

- student submits either audio or beginner skip;
- both together is rejected;
- neither is rejected;
- beginner skip auto-places into level 1;
- resubmission updates the existing placement;
- reviewed placements follow the existing one-way review rule;
- review remains lead-only;
- private audio remains private.

---

# 16. Placement Review

The original product rule remains:

```text
lead-only review
```

The reviewer must also be:

```text
an active member of the placement's academy
```

Therefore:

```text
Lead A + Placement A -> allowed
Lead A + Placement B -> denied
```

Do not use:

```text
User.role == lead
```

as the sole authorization test.

Querysets for pending placements must be academy-scoped.

---

# 17. Audio Access

Preserve Phase 6 private storage.

The tenant-aware access chain is:

```text
authenticated requester
        |
        v
active membership
        |
        v
placement belongs to academy
        |
        v
existing Phase 6 audio permission
        |
        v
short-lived signed URL
```

Do not restore:

```text
MEDIA_URL
```

or a public placement-media route.

Do not widen access to parents or sub-teachers during this phase.

---

# 18. Curriculum Permissions

Recommended baseline:

| Action | Owner | Admin | Staff | Teacher | Student | Parent |
|---|---:|---:|---:|---:|---:|---:|
| View academy curriculum | Yes | Yes | Yes | Yes | Yes | Yes where needed |
| Create Track | Yes | Yes | No | Policy-dependent | No | No |
| Update Track | Yes | Yes | No | Policy-dependent | No | No |
| Create/update Level | Yes | Yes | No | Policy-dependent | No | No |
| Configure teacher tracks | Yes | Yes | No | Own only if explicitly allowed | No | No |
| Submit placement | No | No | No | No | Yes | Existing parent flow only |
| Review placement | Existing lead-only rule + active academy membership | | | | | |

Do not derive academy authority from the global `User.role`.

Use the accepted Phase 1/2 organization permission system.

---

# 19. Public Curriculum Endpoint

Original Phase 2 exposed:

```text
GET /api/curriculum/tracks/
```

as a public single-academy endpoint.

That assumption is no longer safe for a multi-tenant platform.

The Phase 3 default should be:

```text
academy-owned curriculum = academy-scoped data
```

Preferred public-facing direction:

```text
GET /api/curriculum/organizations/{organization_id}/tracks/
```

with appropriate authentication and membership checks.

If the legacy endpoint must remain for compatibility, it must not return every academy's private tracks.

Give it an explicit documented purpose, or safely retire it after checking existing clients/tests.

Never leave a global endpoint that silently lists the entire multi-tenant curriculum.

---

# 20. Template Copying

A platform curriculum-template library is optional.

Do not build full template versioning in this phase.

If copying is implemented:

```text
Platform template
      |
      +--> Academy A Track/Levels
      |
      +--> Academy B Track/Levels
```

The copies must be independent database records.

Editing Academy A's copy must not affect:

- Academy B's copy;
- the platform template.

Do not introduce shared mutable curriculum records.

---

# 21. Data Integrity

Enforce these invariants in model/service code and through API permissions.

## Track

```text
organization exists
slug unique within organization
```

## Level

```text
track belongs to an organization
Level uses its track's organization
existing ordering rules remain valid
```

## Placement

```text
student is a student account
student is active in placement.organization
track belongs to placement.organization
recommended_level.track_id == track_id
```

## Review

```text
reviewer satisfies existing lead rule
reviewer is active in placement.organization
```

## TeacherTrack

```text
membership.organization == track.organization
membership represents an eligible teacher account
```

## Parent

```text
parent active in organization
student active in organization
ParentLink exists
```

Do not rely only on serializers.

---

# 22. Queryset Isolation

Organization-aware views must scope their querysets.

Examples:

```python
Track.objects.filter(organization=organization)
```

Teacher curriculum:

```python
TeacherTrack.objects.filter(
    membership__organization=organization,
    track__organization=organization,
)
```

Placements:

```python
PlacementResult.objects.filter(
    track__organization=organization,
    student__organization_memberships__organization=organization,
    student__organization_memberships__status=MembershipStatus.ACTIVE,
)
```

Use the repository's actual model/enum names.

The important invariant is that the query itself is tenant-scoped.

---

# 23. Object-ID Isolation Tests

Build at least:

```text
Academy A
Academy B

Track A
Track B

Level A
Level B

Student A
Student B

Lead A
Lead B

Teacher T in both academies
```

Then prove:

```text
Academy A member + Track B id -> denied
Academy A member + Level B id -> denied
Academy A member + Placement B id -> denied
Academy A lead + Placement B id -> denied
Student A + Track B id -> denied
Student A + Placement B id -> denied
```

Do not only test list endpoints.

Test detail endpoints, mutation endpoints, nested relationships, and audio URL minting.

---

# 24. Cross-Academy Teacher Tests

Use one global teacher account with memberships in two academies.

Example:

```text
Teacher T

Academy A
    Tajweed

Academy B
    Arabic
```

Prove:

```text
T in A
    can see/use Tajweed assignment
    cannot see/use Arabic assignment as Academy A

T in B
    can see/use Arabic assignment
    cannot see/use Tajweed assignment as Academy B
```

Also prove that an academy-admin in A cannot modify T's Academy B teaching eligibility.

---

# 25. Legacy Curriculum Migration Tests

Test a database starting from the pre-SaaS curriculum state.

Prove:

```text
old Track -> deterministically assigned to academy
old Level -> remains attached to old Track
old PlacementResult -> remains readable in that academy
old teacher specialty -> has a safe tenant-scoped equivalent
```

The migration must be repeatable in the normal deployment path.

Do not silently drop existing curriculum rows.

---

# 26. API Response Shape

Organization-scoped Track responses should expose enough information for the future frontend:

```text
id
name
slug
organization_id
levels
```

Level:

```text
id
track_id
name
order
min_age
group_eligible
```

Placement responses should preserve existing public response semantics while adding whatever verified academy context is required.

Never expose:

- permanent private storage URLs;
- raw bucket paths;
- unrelated academy identifiers;
- internal review fields to unauthorized roles.

---

# 27. OpenAPI / Swagger

Document:

- organization-scoped path parameters;
- authentication requirements;
- active membership requirements;
- role/authority requirements;
- expected success responses;
- forbidden responses;
- not-found behaviour for foreign tenant objects;
- validation errors;
- placement audio URL behaviour.

Swagger must reflect the API that the future frontend will actually consume.

Do not leave the frontend to infer tenancy rules from implementation details.

---

# 28. Backward Compatibility

Do not rewrite the curriculum domain from scratch.

Preserve:

- existing Track/Level field semantics;
- Level ordering;
- PlacementResult state behaviour;
- private audio handling;
- placement resubmission behaviour;
- lead-only review;
- original tests.

Where a global endpoint becomes unsafe, migrate it deliberately rather than silently duplicating it.

---

# 29. Migration Strategy

Suggested safe order:

```text
1. add Track.organization
2. identify the existing academy context
3. migrate existing Tracks
4. enforce Track organization ownership
5. change slug uniqueness to (organization, slug)
6. verify Levels inherit Track ownership safely
7. introduce academy-scoped TeacherTrack
8. backfill legacy specialties where determinable
9. update placement querysets/permissions
10. update placement audio access
11. add organization-scoped API endpoints
12. update Swagger
13. run full regression suite
14. run tenant-isolation suite
15. complete manual/API acceptance
```

Do not drop the legacy specialty relation while scheduling still depends on it.

---

# 30. Tests Required

## Track

- academy-scoped list;
- academy-scoped detail;
- academy-scoped create;
- academy-scoped update;
- same slug allowed across academies;
- same slug rejected within one academy.

## Level

- foreign academy track cannot be used;
- foreign academy level cannot be read/updated;
- ordering remains correct;
- append-only rules remain correct.

## Placement

- only active academy students can submit;
- foreign academy Track id rejected;
- only own academy placements returned;
- foreign academy placement id denied;
- lead review remains lead-only and academy-scoped;
- re-submission still updates rather than duplicates;
- recommended level cannot cross tracks.

## Teacher curriculum

- teacher can be assigned tracks per academy;
- same teacher can have different tracks in different academies;
- teacher cannot configure another academy;
- academy A eligibility does not authorize academy B.

## Parent

- ParentLink alone does not grant access;
- both parent and child must be active in the academy;
- unrelated parent is denied.

## Audio

- Phase 6 private storage tests remain green;
- lead A can access only placement audio in Academy A;
- lead B cannot mint Academy A audio URLs;
- students cannot access another student's or academy's audio.

## Regression

- original Phase 1–8 suite;
- SaaS Phase 1 suite;
- SaaS Phase 2 suite;
- fresh PostgreSQL migration;
- `python manage.py check`;
- `python manage.py makemigrations --check`.

---

# 31. Manual Acceptance Journey

Before marking Phase 3 complete, manually verify through Swagger or the API client:

```text
Academy A
    -> create Track
    -> create Levels
    -> assign teacher to Track
    -> create student membership
    -> submit placement
    -> review placement as Academy A lead
    -> retrieve private audio URL

Academy B
    -> confirm Academy A curriculum is invisible
    -> confirm Academy A placement is inaccessible
    -> confirm Academy A teacher-track configuration is inaccessible
```

Also verify:

```text
Teacher T in Academy A
    -> sees only Academy A teaching tracks

Teacher T in Academy B
    -> sees only Academy B teaching tracks
```

No frontend work is required for this acceptance.

---

# 32. Definition of Done

SaaS Phase 3 is complete only when:

1. Every Track belongs to exactly one Organization.
2. Track slug uniqueness is organization-scoped.
3. Levels remain correctly ordered and tenant-safe through their Track.
4. PlacementResult is academy-safe through student membership and Track ownership.
5. Students cannot access or create placements across academies.
6. Leads cannot review placements across academies.
7. Parent access does not bypass academy membership.
8. Teacher curriculum eligibility is academy-scoped.
9. One teacher can have different track eligibility in different academies.
10. Object-id tenant-isolation tests pass for Track, Level and PlacementResult.
11. Private placement-audio access remains secure.
12. Original Phase 1–8 behaviour remains green.
13. SaaS Phase 1 and Phase 2 suites remain green.
14. Fresh PostgreSQL migration succeeds.
15. `python manage.py check` passes.
16. `python manage.py makemigrations --check` passes.
17. OpenAPI/Swagger documents the organization-scoped curriculum API.
18. Manual/API acceptance is completed.
19. `learnings.md` records important decisions.
20. `tech-debt.md` records deliberate deferred work.
21. The phase is committed before SaaS Phase 4 begins.

---

# 33. Explicitly Out of Scope

Do not implement:

- scheduling tenancy;
- Booking tenancy;
- Availability tenancy;
- routing tenancy;
- changing Booking business rules except for minimal curriculum compatibility;
- pricing tenancy;
- assessment tenancy;
- payout tenancy;
- recurring cohorts;
- notifications;
- onboarding;
- invitations;
- billing;
- frontend;
- public curriculum marketplace;
- curriculum analytics;
- AI placement;
- AI recitation scoring;
- teacher ranking;
- assessment-driven routing.

---

# 34. Stop and Ask Instead of Guessing

Stop before implementation when:

- existing curriculum cannot be deterministically assigned to an academy;
- a Track transfer between academies is required;
- legacy teacher specialty data cannot be safely mapped;
- removing the old specialty field would break existing scheduling behaviour;
- a public multi-academy curriculum catalogue is required;
- parent access to placement audio is proposed to change;
- a requirement would force scheduling, pricing, assessment, payout, or onboarding work into this phase.

Routine model, migration, queryset, serializer, permission, endpoint, admin, test, and documentation work may proceed when the rule is already explicit.
