# API Inventory

## GET /api/accounts/me/

**Description**: GET /api/accounts/me/ — the logged-in user's own profile.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Status Codes**: 200

## GET /api/accounts/my-children/

**Description**: GET /api/accounts/my-children/ — students linked to the calling parent.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Status Codes**: 200

## GET /api/accounts/organizations/{organization_pk}/children/

**Description**: GET /api/accounts/organizations/{id}/children/ — the caller's children *here*.

The endpoint SaaS Phase 2 exists for. ``/my-children/`` answers "who are this
parent's children", globally and correctly; this one answers "which of them is a
student of *this* academy", and the difference between the two answers is the
tenant boundary.

Both sides must be active members: a parent whose own membership is suspended
gets an empty list, and a linked child the academy has not admitted (or has
suspended) is absent from it. So a real, global parent-child relationship cannot
be used to make Academy A show a student who belongs to Academy B —
``tenancy.children_in_organization()`` is the one place that rule lives.

A non-member is refused by ``IsOrganizationMember`` before the queryset runs,
and a caller who is not a parent account gets the same 403
``/my-children/`` gives them. The fields are the same global ones too: no
signup code, and nothing the academy owns.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 401, 403

## GET /api/accounts/organizations/{organization_pk}/teacher-configurations/

**Description**: /api/accounts/organizations/{id}/teacher-configurations/ — owner/admin only.

GET lists this academy's teaching terms; POST gives an existing member theirs.
Staff and teacher members are refused both, the same narrower default the
membership directory takes — what an academy pays its teachers is not something
an ordinary member reads, and a narrow rule can be widened safely later.

POST names a ``user``, and the membership is resolved inside this academy, so
the request cannot reach a membership it does not administer. Only a ``lead`` or
``sub`` account may be given teaching terms: an organization role is authority,
not a teaching identity, and Phase 2 preserves the account domain's rule about
who can teach.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 401, 403

## POST /api/accounts/organizations/{organization_pk}/teacher-configurations/

**Description**: /api/accounts/organizations/{id}/teacher-configurations/ — owner/admin only.

GET lists this academy's teaching terms; POST gives an existing member theirs.
Staff and teacher members are refused both, the same narrower default the
membership directory takes — what an academy pays its teachers is not something
an ordinary member reads, and a narrow rule can be widened safely later.

POST names a ``user``, and the membership is resolved inside this academy, so
the request cannot reach a membership it does not administer. Only a ``lead`` or
``sub`` account may be given teaching terms: an organization role is authority,
not a teaching identity, and Phase 2 preserves the account domain's rule about
who can teach.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 401, 403

## GET /api/accounts/organizations/{organization_pk}/teacher-configurations/{id}/

**Description**: GET/PATCH one set of teaching terms — approve, re-cap the week, re-rate the hour.

The endpoint that proves the phase: PATCHing a teacher's terms here changes what
*this* academy asks of them and nothing about any other academy they work for,
because the row being written belongs to one membership. A configuration id from
another tenant is a 404 from the scoped queryset.

PUT is not offered. A whole-object replace would have to accept ``membership``,
which is not editable — terms pointing at a different person or academy are
different terms.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200, 401, 403, 404

## PATCH /api/accounts/organizations/{organization_pk}/teacher-configurations/{id}/

**Description**: GET/PATCH one set of teaching terms — approve, re-cap the week, re-rate the hour.

The endpoint that proves the phase: PATCHing a teacher's terms here changes what
*this* academy asks of them and nothing about any other academy they work for,
because the row being written belongs to one membership. A configuration id from
another tenant is a 404 from the scoped queryset.

PUT is not offered. A whole-object replace would have to accept ``membership``,
which is not editable — terms pointing at a different person or academy are
different terms.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 200, 400, 401, 403, 404

## POST /api/accounts/parent-links/

**Description**: POST /api/accounts/parent-links/ — parent links to a student by code.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Request Body**: Yes
- **Status Codes**: 201

## GET /api/assessment/organizations/{organization_pk}/{id}/

**Description**: GET /api/assessment/organizations/<organization_pk>/{id}/ — the lead opens one assessment in full.

The drill-down from the review queue: booking, student, teacher, rubric
snapshot, scores, flag reason and any review note. Lead-only, and reading it
does **not** mark it reviewed — the spec is explicit that clearing a flag is an
explicit action, so the review stamp only ever comes from the POST below.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200

## POST /api/assessment/organizations/{organization_pk}/{id}/review/

**Description**: POST /api/assessment/organizations/<organization_pk>/{id}/review/ — the lead annotates and marks reviewed.

Writes three fields and no others. The teacher's scores, summary and flag are
untouched, and the model refuses to change them on an existing row, so "review
never rewrites historical teacher data" holds even for a caller that tries.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 200, 403, 404

## POST /api/assessment/organizations/{organization_pk}/bookings/{booking_id}/

**Description**: POST /api/assessment/organizations/<organization_pk>/bookings/{booking_id}/ — the teacher who taught it scores it.

Two layers keep this to the right teacher and the right booking. The queryset
is scoped to ``teacher=request.user``, so another teacher's session is a **404**
rather than a 403 — a 403 would confirm the booking exists. And
``SessionAssessment.clean()`` re-checks ``assessed_by == booking.teacher``
anyway, so a future caller that skips this view cannot get it wrong either.

A booking that is scheduled, cancelled or a no-show is a **400** carrying the
model's own message: it exists and belongs to this teacher, it is simply not a
session that happened. Submitting an assessment does not mark a booking
completed — that stays a separate act.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - booking_id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 403, 404

## GET /api/assessment/organizations/{organization_pk}/child/

**Description**: GET /api/assessment/organizations/<organization_pk>/child/?student_id= — a linked child's assessment history.

Same family shape as ``/mine/``, reached through the ``ParentLink`` check.
Not in the spec's endpoint list, but the spec's visibility table gives a parent
"linked-child assessments/progress" and progress alone would not be that.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - student_id (query)
  - track_id (query)
- **Status Codes**: 200, 403

## GET /api/assessment/organizations/{organization_pk}/mine/

**Description**: GET /api/assessment/organizations/<organization_pk>/mine/ — the student's own assessment history.

Family shape: the teacher's summary, the criterion scores and comments, and no
internal quality-control field at all. Scoped to ``student=request.user``, so
this endpoint cannot be pointed at anybody else.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - track_id (query)
- **Status Codes**: 200

## GET /api/assessment/organizations/{organization_pk}/progress/child/

**Description**: GET /api/assessment/organizations/<organization_pk>/progress/child/?student_id=&track_id= — a linked child's.

The ``ParentLink`` check is the whole endpoint: an unrelated parent asking about
somebody else's child is refused, and the refusal happens before any progress is
computed, not by omitting fields from a response that was built anyway.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - from (query)
  - organization_pk (path)
  - student_id (query)
  - to (query)
  - track_id (query)
- **Status Codes**: 200, 400, 403

## GET /api/assessment/organizations/{organization_pk}/progress/mine/

**Description**: GET /api/assessment/organizations/<organization_pk>/progress/mine/?track_id= — the student's own progress.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - from (query)
  - organization_pk (path)
  - to (query)
  - track_id (query)
- **Status Codes**: 200, 400, 403

## GET /api/assessment/organizations/{organization_pk}/reports/teachers/

**Description**: GET /api/assessment/organizations/<organization_pk>/reports/teachers/ — lead-only quality visibility.

Optionally narrowed by ``from``/``to`` and ``track_id``. Per teacher: how many
sessions they assessed, the overall average, the average per track, how many
they flagged, and how many of those flags are still waiting on the lead.

Two things it is not. It is **not** a leaderboard: rows come back ordered by
username and carry no rank. And it does **not** invent rows — a teacher who
assessed nothing in the window is absent rather than present with 0.00, because
a missing assessment is missing data and a zero next to someone's name is an
accusation.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - from (query)
  - organization_pk (path)
  - to (query)
  - track_id (query)
- **Status Codes**: 200, 400, 403

## GET /api/assessment/organizations/{organization_pk}/review/queue/

**Description**: GET /api/assessment/organizations/<organization_pk>/review/queue/ — flagged and not yet reviewed.

One definition of the queue, ``SessionAssessment.pending_lead_review()``, so
the count in the teacher report and the list here cannot disagree. A reviewed
assessment leaves it; the flag itself stays on the record, because that the
teacher raised one is history.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200

## GET /api/assessment/organizations/{organization_pk}/rubrics/

**Description**: /api/assessment/organizations/<organization_pk>/rubrics/ — the lead configures what teachers score against.

* **GET ?track_id=** lists rubrics, newest first, superseded ones included:
  "what do we score Tajweed on, and what did we score it on before" is one
  question. Without ``track_id`` it lists every track's.
* **POST** creates a rubric with its criteria. If the track already has an
  active rubric, that one is superseded rather than edited — see
  ``AssessmentRubricCreateSerializer``.

Lead-only on both sides. A rubric decides what every teacher in the academy is
measured on, so it is not something a sub-teacher configures for themselves.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - track_id (query)
- **Status Codes**: 200, 403

## POST /api/assessment/organizations/{organization_pk}/rubrics/

**Description**: /api/assessment/organizations/<organization_pk>/rubrics/ — the lead configures what teachers score against.

* **GET ?track_id=** lists rubrics, newest first, superseded ones included:
  "what do we score Tajweed on, and what did we score it on before" is one
  question. Without ``track_id`` it lists every track's.
* **POST** creates a rubric with its criteria. If the track already has an
  active rubric, that one is superseded rather than edited — see
  ``AssessmentRubricCreateSerializer``.

Lead-only on both sides. A rubric decides what every teacher in the academy is
measured on, so it is not something a sub-teacher configures for themselves.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 403

## GET /api/assessment/organizations/{organization_pk}/rubrics/{id}/

**Description**: /api/assessment/organizations/<organization_pk>/rubrics/{id}/ — read one rubric, or edit it in place.

PATCH is the operation the historical-data rule is about: renaming or retiring
a criterion changes what *future* assessments look like and leaves every
existing one exactly as it was, because each score carries the criterion name
it was submitted under. Nothing here rewrites an ``AssessmentScore``, and the
model would refuse if it tried.

PUT is not offered. A whole-object replace on live configuration whose
criteria are referenced by historical records is not a coherent operation;
replacing a rubric wholesale is a POST to the collection, which supersedes.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200

## PATCH /api/assessment/organizations/{organization_pk}/rubrics/{id}/

**Description**: /api/assessment/organizations/<organization_pk>/rubrics/{id}/ — read one rubric, or edit it in place.

PATCH is the operation the historical-data rule is about: renaming or retiring
a criterion changes what *future* assessments look like and leaves every
existing one exactly as it was, because each score carries the criterion name
it was submitted under. Nothing here rewrites an ``AssessmentScore``, and the
model would refuse if it tried.

PUT is not offered. A whole-object replace on live configuration whose
criteria are referenced by historical records is not a coherent operation;
replacing a rubric wholesale is a POST to the collection, which supersedes.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 200, 400, 403

## POST /api/assessment/organizations/{organization_pk}/snapshots/

**Description**: POST /api/assessment/organizations/<organization_pk>/snapshots/ — the lead freezes a period.

An explicit lead action, deliberately: automatic snapshot jobs are out of scope
for this phase. Repeating a request for the same student, track and period
returns the **existing** row with a **200** and changes nothing, which is the
spec's duplicate rule — a snapshot that moved when you asked for it twice would
not be a snapshot.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 200, 400, 403

## GET /api/assessment/organizations/{organization_pk}/snapshots/all/

**Description**: GET /api/assessment/organizations/<organization_pk>/snapshots/?student_id=&track_id= — the lead's own view.

Unpublished rows included: the lead generated them, and needs to see what is
waiting to be released.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - student_id (query)
  - track_id (query)
- **Status Codes**: 200

## GET /api/assessment/organizations/{organization_pk}/snapshots/child/

**Description**: GET /api/assessment/organizations/<organization_pk>/snapshots/child/?student_id=&track_id= — a linked child's.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - student_id (query)
  - track_id (query)
- **Status Codes**: 200, 403

## GET /api/assessment/organizations/{organization_pk}/snapshots/mine/

**Description**: GET /api/assessment/organizations/<organization_pk>/snapshots/mine/?track_id= — the student's published snapshots.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - track_id (query)
- **Status Codes**: 200

## GET /api/assessment/organizations/{organization_pk}/teacher/mine/

**Description**: GET /api/assessment/organizations/<organization_pk>/teacher/mine/ — what this teacher has submitted.

Their own rows only, including their own flags. Not the lead's review notes:
the spec gives a sub-teacher no access to those in this phase. The lead sees
their own submissions here too — for academy-wide data they use the review
queue and the report.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200

## POST /api/auth/login/

**Description**: Check the credentials and return the REST Token
if the credentials are valid and authenticated.
Calls Django Auth login method to register User ID
in Django session framework

Accept the following POST parameters: username, password
Return the REST Framework Token Object's key.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Request Body**: Yes
- **Status Codes**: 200

## POST /api/auth/logout/

**Description**: Calls Django logout method and delete the Token object
assigned to the current User object.

Accepts/Returns nothing.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Status Codes**: 200

## POST /api/auth/register/

**Description**: POST /api/auth/register/ — create a user.

A minor student comes back with status ``pending_parent_link``: the account
exists and can log in, but is not complete until a parent links to it.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Request Body**: Yes
- **Status Codes**: 201

## GET /api/curriculum/organizations/{organization_pk}/levels/

**Description**: /api/curriculum/organizations/{id}/levels/ — the academy's ladders.

Flat rather than nested under a track, with ``?track=<id>`` to narrow it: a
client rendering an academy's whole curriculum wants one request, and the
ownership check is no less clear for it because the track is validated against
the route's academy either way.

POST appends. The next ``order`` is computed server-side, so "insert at
position 2" is not a request this API can express — which is how the Phase 2
append-only rule survives the arrival of a write endpoint.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - track (query)
- **Status Codes**: 200, 401, 403

## POST /api/curriculum/organizations/{organization_pk}/levels/

**Description**: /api/curriculum/organizations/{id}/levels/ — the academy's ladders.

Flat rather than nested under a track, with ``?track=<id>`` to narrow it: a
client rendering an academy's whole curriculum wants one request, and the
ownership check is no less clear for it because the track is validated against
the route's academy either way.

POST appends. The next ``order`` is computed server-side, so "insert at
position 2" is not a request this API can express — which is how the Phase 2
append-only rule survives the arrival of a write endpoint.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 401, 403

## GET /api/curriculum/organizations/{organization_pk}/levels/{id}/

**Description**: GET/PATCH one level. Its name and its description, never its position.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200, 401, 403, 404

## PATCH /api/curriculum/organizations/{organization_pk}/levels/{id}/

**Description**: GET/PATCH one level. Its name and its description, never its position.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 200, 400, 401, 403, 404

## POST /api/curriculum/organizations/{organization_pk}/placements/

**Description**: POST .../placements/ — a student of this academy submits audio or a skip.

Two gates, both required. ``IsStudent`` is the account role, unchanged from
Phase 2; ``IsOrganizationMember`` is the tenant, and it is what stops a student
enrolled at Academy A from submitting into Academy B by knowing its id. The
track then resolves inside this academy only, and ``PlacementResult.clean()``
re-checks the membership at the model layer, so the admin and a direct ORM
write are held to the same rule.

Re-submitting for a track the student already has a placement for updates that
row back to pending; it never creates a second one. A minor with no parent link
yet may still submit — placement is a pre-enrolment step, and
``is_fully_active`` gates booking, not this.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 401, 403

## GET /api/curriculum/organizations/{organization_pk}/placements/{id}/audio-url/

**Description**: GET .../placements/{id}/audio-url/ — temporary access to a sample.

Phase 6's authorisation step, with the academy check in front of it. Minting
the URL *is* the authorisation, so the whole question of who may hear a
recitation sample is decided here, and SaaS Phase 3 answers it with a
conjunction:

.. code-block:: text

    authenticated
        + active member of this academy
        + placement belongs to this academy
        + Phase 6's rule: the lead teacher, or the student themselves

Nothing was widened. Sub-teachers and parents are still refused — a minor's
voice recording is the last thing a tenancy phase should open up — and the
roles are exactly Phase 6's.

Everything unauthorised is a 404 rather than a 403, because the queryset is
what narrows it: another academy's placement, another student's placement and a
beginner skip with no recording all answer the same way, and none of them
confirms that a row exists.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200, 401, 403, 404

## POST /api/curriculum/organizations/{organization_pk}/placements/{id}/review/

**Description**: POST .../placements/{id}/review/ — this academy's lead sets the level.

``Lead A + Placement A`` is allowed and ``Lead A + Placement B`` is a 404, from
the scoped queryset rather than from a permission class — the placement is not
in this academy's queryset, so the response cannot confirm it exists. The
model refuses the same thing again in ``clean()``: a reviewer must be an active
member of the academy that owns the track.

Review is still a one-way transition: an already-reviewed placement returns
409. Correcting a level means the admin, or the student re-submitting.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 200, 400, 401, 403, 404, 409

## GET /api/curriculum/organizations/{organization_pk}/placements/children/

**Description**: GET .../placements/children/ — a parent's linked children, in this academy.

Three conditions, and the phase spec is explicit that all three must hold: the
parent is active here, the child is active here, and a ``ParentLink`` exists.
``accounts.tenancy.children_in_organization()`` is where that rule lives and
this view calls it rather than re-deriving it — a second copy is the one that
would eventually disagree, and the disagreement would be a parent reading
another academy's student.

A ``ParentLink`` is a global family fact, not a key: the same parent sees
different children through Academy A's route and Academy B's.

Read-only, and no audio. Phase 6 decided that a recitation sample is for the
lead teacher and the student themselves, and SaaS Phase 3 is forbidden from
widening that — so a parent can see *that* their child submitted a sample and
where they were placed, and cannot hear it.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 401, 403

## GET /api/curriculum/organizations/{organization_pk}/placements/mine/

**Description**: GET .../placements/mine/ — the caller's own placements *in this academy*.

A student studying at two academies calls this twice, once per route, and gets
two disjoint lists. Their global set of placements is not something any single
academy is shown.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 401, 403

## GET /api/curriculum/organizations/{organization_pk}/placements/pending/

**Description**: GET .../placements/pending/ — this academy's review queue.

The endpoint Phase 2 got most wrong for a multi-tenant platform: it handed
every pending placement on the platform to any lead teacher who asked. Now the
queue is the intersection of "a lead teacher" and "a member of this academy",
and it contains only placements whose track this academy owns and whose student
it has admitted.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 401, 403

## GET /api/curriculum/organizations/{organization_pk}/teachers/

**Description**: /api/curriculum/organizations/{id}/teachers/ — who may teach what here.

Owner and admin only, for reading as well as writing — the one curriculum
endpoint that is not readable by every member. It follows ``accounts``'
teacher-configurations endpoint rather than the track and level endpoints
above, because the list names people and what the academy has entrusted them
with, which is closer to the membership directory than to a syllabus. The
teacher themselves reads it from ``teachers/mine/``, so nothing here is hidden
from the person it is about.

The membership is resolved from a ``user`` id *inside this academy*, so an
administrator of one academy cannot touch the same teacher's eligibility in
another — the phase spec's cross-academy teacher requirement, enforced by the
lookup rather than by a check that could be forgotten.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 401, 403

## POST /api/curriculum/organizations/{organization_pk}/teachers/

**Description**: /api/curriculum/organizations/{id}/teachers/ — who may teach what here.

Owner and admin only, for reading as well as writing — the one curriculum
endpoint that is not readable by every member. It follows ``accounts``'
teacher-configurations endpoint rather than the track and level endpoints
above, because the list names people and what the academy has entrusted them
with, which is closer to the membership directory than to a syllabus. The
teacher themselves reads it from ``teachers/mine/``, so nothing here is hidden
from the person it is about.

The membership is resolved from a ``user`` id *inside this academy*, so an
administrator of one academy cannot touch the same teacher's eligibility in
another — the phase spec's cross-academy teacher requirement, enforced by the
lookup rather than by a check that could be forgotten.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 401, 403

## GET /api/curriculum/organizations/{organization_pk}/teachers/{id}/

**Description**: GET/PATCH one assignment — withdraw or restore eligibility.

An assignment id from another academy is a 404 from the scoped queryset, which
is what stops an administrator here from deactivating a teacher's eligibility
there. PUT is not offered: ``membership`` and ``track`` are what the row *is*.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200, 401, 403, 404

## PATCH /api/curriculum/organizations/{organization_pk}/teachers/{id}/

**Description**: GET/PATCH one assignment — withdraw or restore eligibility.

An assignment id from another academy is a 404 from the scoped queryset, which
is what stops an administrator here from deactivating a teacher's eligibility
there. PUT is not offered: ``membership`` and ``track`` are what the row *is*.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 200, 400, 401, 403, 404

## GET /api/curriculum/organizations/{organization_pk}/teachers/mine/

**Description**: GET .../teachers/mine/ — what the calling teacher may teach *here*.

The endpoint that makes the phase visible to a teacher who works for two
academies. The same account calling it through Academy A's route and Academy
B's route gets two different lists from the same global identity, because the
queryset is keyed on the membership the URL resolved to — not on the user.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 401, 403

## GET /api/curriculum/organizations/{organization_pk}/tracks/

**Description**: /api/curriculum/organizations/{id}/tracks/ — the academy's own subjects.

GET is open to every active member: a student needs the track list to submit a
placement and a teacher needs it to know what the academy teaches. POST is the
owner's or an administrator's, because adding a subject is an act of running
the business (the phase spec leaves teacher authoring to policy; see
``permissions.CURRICULUM_MANAGER_ROLES``).

The created track belongs to the academy in the *route*, whatever the body
says — there is no ``organization`` field on the write shape at all.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 401, 403

## POST /api/curriculum/organizations/{organization_pk}/tracks/

**Description**: /api/curriculum/organizations/{id}/tracks/ — the academy's own subjects.

GET is open to every active member: a student needs the track list to submit a
placement and a teacher needs it to know what the academy teaches. POST is the
owner's or an administrator's, because adding a subject is an act of running
the business (the phase spec leaves teacher authoring to policy; see
``permissions.CURRICULUM_MANAGER_ROLES``).

The created track belongs to the academy in the *route*, whatever the body
says — there is no ``organization`` field on the write shape at all.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 401, 403

## GET /api/curriculum/organizations/{organization_pk}/tracks/{id}/

**Description**: GET/PATCH one of this academy's tracks.

PATCH renames it or changes its slug. It cannot move the track to another
academy: ``organization`` is not on the write shape, and ``Track.clean()``
refuses the change even from the admin or a direct ORM write, because moving a
track carries its levels, placements and teacher assignments into an academy
that created none of them.

PUT is not offered — a whole-object replace would have to accept
``organization``.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200, 401, 403, 404

## PATCH /api/curriculum/organizations/{organization_pk}/tracks/{id}/

**Description**: GET/PATCH one of this academy's tracks.

PATCH renames it or changes its slug. It cannot move the track to another
academy: ``organization`` is not on the write shape, and ``Track.clean()``
refuses the change even from the admin or a direct ORM write, because moving a
track carries its levels, placements and teacher assignments into an academy
that created none of them.

PUT is not offered — a whole-object replace would have to accept
``organization``.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 200, 400, 401, 403, 404

## GET /api/curriculum/placements/{id}/audio/

**Description**: GET /api/curriculum/placements/{id}/audio/?token=... — the local path.

Only used when the storage backend cannot sign its own URLs, i.e. local
private storage in development and tests. Against a private bucket the
minted URL is presigned and points at the bucket, so nothing reaches here.

**Deliberately not organization-scoped, and that is not a hole.** The token is
a bearer capability standing in for a presigned URL, minted only after
``AcademyPlacementAudioURLView`` has checked the caller's membership, the
placement's academy and the Phase 6 role rule. Requiring a tenant in the path
here would add a check that proves nothing — the holder of the token is
authorised by having been given it — while an ``<audio src>`` element cannot
send an Authorization header, which is exactly why presigned URLs exist.

What it does check, on every request:

* the token's signature and its age (``read_audio_token``);
* that the token names *this* placement — a token for one sample cannot be
  pointed at another by editing the path;
* that the placement still holds the object the token was minted for, so a
  re-submitted sample invalidates outstanding tokens immediately rather than
  at expiry.

Every failure is the same 404. Telling the caller which check failed would
let someone probing tokens narrow down why.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - token (query)
- **Status Codes**: 200, 404

## POST /api/imports/organizations/{organization_pk}/{id}/commit/

**Description**: Commit a validated import job

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200

## POST /api/imports/organizations/{organization_pk}/validate/

**Description**: Resolves the URL's organization to the caller's own membership, once.

The permission classes and the querysets both need the same answer to "is this
caller inside this tenant, and as what", and asking twice is how the two
eventually disagree. So it is resolved here, cached for the request, and read
from ``view.caller_membership`` by everything else.

``None`` means no access — non-member, suspended member and anonymous caller
alike — and the permission layer turns that into a refusal before any handler
runs. Which is why ``organization`` may assume it is not None: by the time a
handler executes, the caller has an active membership in this organization.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201

## GET /api/notifications/organizations/{organization_pk}/{id}/

**Description**: GET /api/notifications/organizations/<organization_pk>/<id>/ — notification detail.

Owner/admin can read any notification in this academy.
Regular members can only read their own notifications in this academy.
Notifications in other academies or belonging to other users return 404.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200

## POST /api/notifications/organizations/{organization_pk}/{id}/read/

**Description**: POST /api/notifications/organizations/<organization_pk>/<id>/read/ — mark a notification as read.

A recipient can only mark their own notification as read.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200, 404

## GET /api/notifications/organizations/{organization_pk}/admin/

**Description**: GET /api/notifications/organizations/<organization_pk>/admin/ — academy-wide notification history (admin only).

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200

## GET /api/notifications/organizations/{organization_pk}/deliveries/

**Description**: GET /api/notifications/organizations/<organization_pk>/deliveries/ — academy delivery logs (admin only).

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200

## GET /api/notifications/organizations/{organization_pk}/mine/

**Description**: GET /api/notifications/organizations/<organization_pk>/mine/ — user's own notifications in this academy.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200

## POST /api/organizations/

**Description**: POST /api/organizations/ — any authenticated user may found an academy.

The creator becomes the organization's single ``owner`` in the same
transaction, which the serializer guarantees; there is no request in which one
happens without the other. Their ``accounts`` role is left exactly as it was:
a student who founds an academy owns it and is still a student.

No permission class beyond authentication. Who may create a tenant is a
signup/billing question, and Phase 1 has neither — restricting it now would be
inventing a product rule the spec explicitly leaves to later phases.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Request Body**: Yes
- **Status Codes**: 201, 400, 401

## GET /api/organizations/{organization_pk}/audit-logs/

**Description**: GET /api/organizations/{organization_pk}/audit-logs/
List audit logs for an organization.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - action (query)
  - actor (query)
  - created_after (query)
  - created_before (query)
  - object_id (query)
  - object_type (query)
  - organization_pk (path)
  - page (query)
  - page_size (query)
- **Status Codes**: 200, 401, 403

## GET /api/organizations/{organization_pk}/audit-logs/{id}/

**Description**: GET /api/organizations/{organization_pk}/audit-logs/{id}/
Retrieve a specific audit log for an organization.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200, 401, 403, 404

## GET /api/organizations/{organization_pk}/memberships/

**Description**: /api/organizations/{id}/memberships/ — the tenant's directory, owner/admin only.

GET lists the organization's memberships; POST adds an existing user as
``admin``, ``staff`` or ``teacher``. Staff and teacher members are refused
both: an ordinary member does not receive the academy's member directory in
Phase 1, and cannot change who is in it.

The organization a new membership lands in comes from ``self.organization`` —
the caller's *verified* membership — so it cannot be a tenant the caller was
not admitted to, whatever the request body says. ``owner`` is not an
assignable role, so no request here can create a second owner.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 401, 403

## POST /api/organizations/{organization_pk}/memberships/

**Description**: /api/organizations/{id}/memberships/ — the tenant's directory, owner/admin only.

GET lists the organization's memberships; POST adds an existing user as
``admin``, ``staff`` or ``teacher``. Staff and teacher members are refused
both: an ordinary member does not receive the academy's member directory in
Phase 1, and cannot change who is in it.

The organization a new membership lands in comes from ``self.organization`` —
the caller's *verified* membership — so it cannot be a tenant the caller was
not admitted to, whatever the request body says. ``owner`` is not an
assignable role, so no request here can create a second owner.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 401, 403

## PATCH /api/organizations/{organization_pk}/memberships/{id}/

**Description**: PATCH /api/organizations/{id}/memberships/{member_id}/ — suspend, reactivate, re-role.

The three membership-management operations the phase spec gives owner and
admin, behind one partial update: ``status`` suspends or reactivates,
``role`` moves a member between ``admin``, ``staff`` and ``teacher``. The row
is never deleted — a suspended membership is a record that someone was here.

Two things this endpoint refuses. The owner's membership, to anyone including
the owner, because demoting or suspending it is an ownership transfer rather
than a role edit. And a membership belonging to another organization, which is
a 404 from the queryset: the URL's tenant is the only one whose rows are
visible here.

PUT is not offered. A whole-object replace would have to accept
``organization`` and ``user``, and neither is editable — a membership pointing
at a different tenant or a different person is a different membership.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 200, 400, 401, 403, 404

## GET /api/organizations/{id}/

**Description**: GET /api/organizations/{id}/ — one academy, for its members only.

Knowing the id is not access. A non-member and a suspended member both get
403, and the object is fetched through the caller's own membership rather than
from ``Organization.objects``, so there is no queryset here that could return a
tenant the caller does not belong to.

Every active role may read the academy — owner, admin, staff and teacher. It is
the organization's own name, slug and timezone; the things worth protecting
inside a tenant are its members and, in later phases, its students, schedules
and money.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
- **Status Codes**: 200, 401, 403

## GET /api/organizations/mine/

**Description**: GET /api/organizations/mine/ — the academies the caller belongs to.

One entry per active membership, each carrying the organization, the caller's
role in it and the membership's status — enough for a client to know where the
user can act and as what. A user in two academies sees two entries.

Suspended memberships are absent, which is the same rule every other endpoint
here applies: an active membership is what grants tenant access, and a
suspended member reading the academy's name and timezone through this listing
would be the one hole in it. Telling a suspended member *why* they lost access
is a product decision for the onboarding phase (see learnings.md).

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Status Codes**: 200, 401

## POST /api/payouts/organizations/{organization_pk}/{id}/finalize/

**Description**: POST /api/payouts/organizations/<organization_pk>/<int:pk>/finalize/ — make one payout history.

Owner/admin-only, and one-way: after this the record refuses every write, including
the lead's own. A payout that is already finalized answers 409 rather than
silently succeeding, because a second finalization means the caller is acting
on a stale copy of a financial record.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200, 403, 404, 409

## POST /api/payouts/organizations/{organization_pk}/generate/

**Description**: POST /api/payouts/organizations/<organization_pk>/generate/ — turn a period's completed sessions into payouts.

Safe to repeat, which is the property that makes it usable: a lead who is not
sure whether the run went through can simply run it again, and the second run
creates nothing. Existing records are never recalculated, so a rate raised
yesterday does not reprice last month's payroll.

The response reports both halves of the run. ``skipped`` is not noise: a
sub-teacher's session skipped for ``no_payout_rate`` is the lead's cue to set
a rate, and a group-class seat skipped for ``cohort_session_already_paid``
explains why a busy session produced one record.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 403

## GET /api/payouts/organizations/{organization_pk}/lead/

**Description**: GET /api/payouts/organizations/<organization_pk>/lead/ — academy-wide payout records.

Filterable by ``?teacher_id=`` and by period. Unfiltered it is every payout
record in the academy, which is the owner/admin's to see and nobody else's:
the permission class is the only thing standing between this queryset and a
sub-teacher reading their colleagues' income, which is why it is the *first*
thing the class declares.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - end (query)
  - organization_pk (path)
  - start (query)
  - teacher_id (query)
- **Status Codes**: 200, 400, 403

## GET /api/payouts/organizations/{organization_pk}/mine/

**Description**: GET /api/payouts/organizations/<organization_pk>/mine/ — the caller's own payout records.

Optionally bounded by ``?start=&end=``; unbounded it is the teacher's whole
payout history in this academy, which is theirs to read. The queryset is scoped
to ``request.user`` and ``self.organization``, and there is no parameter that
could widen it — another teacher's records are not a 403 here, they simply are
not in the set.

Generated and finalized records both appear. A teacher seeing only finalized
ones would have no way to check a draft before it becomes history.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - end (query)
  - organization_pk (path)
  - start (query)
- **Status Codes**: 200, 400, 403

## GET /api/payouts/organizations/{organization_pk}/statements/

**Description**: GET /api/payouts/organizations/<organization_pk>/statements/?teacher_id=&start=&end= — any teacher's statement.

The same computation ``statements/mine/`` performs, for a teacher the owner/admin
names. ``teacher_id`` is required, because a statement is about one teacher —
an academy-wide total is a different document and Phase 8 was not asked for
one.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - end (query)
  - organization_pk (path)
  - start (query)
  - teacher_id (query)
- **Status Codes**: 200, 400, 403

## GET /api/payouts/organizations/{organization_pk}/statements/mine/

**Description**: GET /api/payouts/organizations/<organization_pk>/statements/mine/?start=&end= — the teacher's own statement.

A statement is computed from the payout records it lists, so the total is
always the sum of the rows shown underneath it. Both bounds are required: a
statement is a document about a period.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - end (query)
  - organization_pk (path)
  - start (query)
- **Status Codes**: 200, 400, 403

## GET /api/pricing/organizations/{organization_pk}/agreements/

**Description**: /api/pricing/organizations/<organization_pk>/agreements/ — the lead records rates, and reads them back.

Both halves of the spec's surface on one path, because they are one resource:

* **POST** records a pricing exception. Lead-only, with no sub-teacher path at
  all: this is the one lever that moves the lead's own margin (mvp-spec
  section 3). Creating an agreement for a student and level that already has a
  live one supersedes the old row rather than editing or deleting it, so the
  history survives.
* **GET ?student_id=** lists one student's history in this academy — active
  *and* superseded, newest first.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - student_id (query)
- **Status Codes**: 200, 400, 403

## POST /api/pricing/organizations/{organization_pk}/agreements/

**Description**: /api/pricing/organizations/<organization_pk>/agreements/ — the lead records rates, and reads them back.

Both halves of the spec's surface on one path, because they are one resource:

* **POST** records a pricing exception. Lead-only, with no sub-teacher path at
  all: this is the one lever that moves the lead's own margin (mvp-spec
  section 3). Creating an agreement for a student and level that already has a
  live one supersedes the old row rather than editing or deleting it, so the
  history survives.
* **GET ?student_id=** lists one student's history in this academy — active
  *and* superseded, newest first.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 403

## GET /api/pricing/organizations/{organization_pk}/agreements/mine/

**Description**: GET /api/pricing/organizations/<organization_pk>/agreements/mine/ — the student's own live rates.

One row per level at most, because only active agreements are listed: a family
reads what they pay now, not the negotiation that got them there. ``notes`` is
absent from this shape entirely (see serializers.py).

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200, 403

## GET /api/scheduling/organizations/{organization_pk}/availability/

**Description**: GET /api/scheduling/organizations/{id}/availability/?teacher_id= — one teacher's hours.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - teacher_id (query)
- **Status Codes**: 200

## POST /api/scheduling/organizations/{organization_pk}/bookings/

**Description**: POST /api/scheduling/organizations/{id}/bookings/ — a student, or their parent, books a slot.

Validation of the slot itself lives in ``Booking.clean()``; a rejection
arrives here as a 400 carrying the model's own message.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 403

## POST /api/scheduling/organizations/{organization_pk}/bookings/{id}/cancel/

**Description**: POST /api/scheduling/organizations/{id}/bookings/{id}/cancel/ — either party cancels.

The row is kept and its status set; nothing is deleted, because attendance
and payouts need the history later.

The queryset is scoped to bookings the caller is a party to — the student,
the teacher, or a parent linked to the student (a parent may book, so a
parent may cancel) — strictly within the route academy. Anyone else gets a 404
rather than a 403, so this endpoint cannot be used to discover that a
booking exists.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Status Codes**: 200, 403, 404, 409

## GET /api/scheduling/organizations/{organization_pk}/bookings/mine/

**Description**: GET /api/scheduling/organizations/{id}/bookings/mine/ — the student's own sessions.

Past and upcoming, every status, earliest first. Cancelled ones stay
visible: they are history, not noise.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200

## GET /api/scheduling/organizations/{organization_pk}/bookings/teaching/

**Description**: GET /api/scheduling/organizations/{id}/bookings/teaching/ — sessions this teacher teaches.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200

## POST /api/scheduling/organizations/{organization_pk}/cohorts/

**Description**: POST /api/scheduling/organizations/{id}/cohorts/ — the lead opens a group class.

Lead-only: a cohort commits a teacher's time, so it is not something a
sub-teacher grants themselves. Whether the level may run as a group at all,
and whether the teacher teaches its track, are ``Cohort.clean()``'s calls and
arrive here as 400s.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 403

## GET /api/scheduling/organizations/{organization_pk}/cohorts/open/

**Description**: GET /api/scheduling/organizations/{id}/cohorts/open/?level_id= — cohorts with a seat free.

The same queryset routing's step 1 reads, exposed so a "browse open cohorts"
view can be built on it later without a second definition of "open" existing.
Seat counts are published; the roster is not (see ``CohortSerializer``).

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - level_id (query)
  - organization_pk (path)
- **Status Codes**: 200

## POST /api/scheduling/organizations/{organization_pk}/route/

**Description**: POST /api/scheduling/organizations/{id}/route/ — the system picks who teaches.

Cohort first, then the lead teacher if they have capacity, then a matched
sub-teacher. The algorithm itself is in ``routing.py``; this view only
translates its two outcomes into HTTP.

A refusal is a **409**, not a 200 with ``routed: false`` and not a 400. The
request was valid and was processed — what stands in the way is the state of
the academy's capacity, which is precisely what 409 means, and it is the code
Phase 3's cancel endpoint already uses for "valid request, wrong state". The
body is structured either way, so a frontend can explain the refusal instead
of showing a bare error, which is what the spec asks for.

Phase 5's ``preferred_teacher`` reuses that 409 rather than adding an outcome:
a waitlisted request is still "valid request, wrong state", and the entry it
created is reported inside the same ``considered`` payload. So a client that
already renders the refusal keeps working, and one that wants to say "you are
on Ustadh's list" reads one extra key.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 403, 409

## POST /api/scheduling/organizations/{organization_pk}/waitlist/{id}/promote/

**Description**: POST /api/scheduling/organizations/{id}/waitlist/{id}/promote/ — the lead grants the request.

The whole fulfillment mechanism this phase ships. Automatic offering when a
slot frees up is explicitly out of scope (spec, tech-debt.md): it needs
background jobs and notification delivery, which is a phase of its own.

Lead-only, for the same reason opening a cohort is: it commits a teacher's
time. The booking goes through ``routing.promote_waitlist_entry``, so it takes
the teacher's lock and re-validates every rule — an entry made last week
against a teacher who has since filled up is refused with a 400 rather than
forced through, which is acceptance criterion 8.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - id (path)
  - organization_pk (path)
- **Request Body**: Yes
- **Status Codes**: 201, 400, 403, 409

## GET /api/scheduling/organizations/{organization_pk}/waitlist/for-teacher/

**Description**: GET /api/scheduling/organizations/{id}/waitlist/for-teacher/?teacher_id= — the queue to work.

Open entries only, priority desc then longest-waiting, which comes from
``TeacherWaitlist.Meta.ordering`` rather than being re-stated here — the
endpoint and any future automatic offer must agree about who is next.

Lead-only. A sub-teacher reading who is waiting for *them* is a reasonable
thing to want and a different decision (it exposes other families' requests
to a teacher who cannot act on them), so it is deliberately not folded in
here — see tech-debt.md.

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
  - teacher_id (query)
- **Status Codes**: 200

## GET /api/scheduling/organizations/{organization_pk}/waitlist/mine/

**Description**: GET /api/scheduling/organizations/{id}/waitlist/mine/ — the family's own entries and status.

Open and fulfilled both, newest-priority-first per ``Meta.ordering``. A
fulfilled entry stays visible because it is the record of a request that was
honoured — "you asked for Ustadh in March and got the 4th of April" is the
history the phase exists to keep.

A **parent** reads their linked children's entries here as well as a student
reading their own (product owner's call, 2026-08-25). ``/route/`` is
``IsStudentOrParent``, so a parent is one of the two people who can *create*
an entry — and a requester who cannot then read their own request back is a
hole, not a privacy boundary. Scoped through ``ParentLink`` exactly as
``BookingCancelView`` scopes cancellation, and for the same reason: a parent
acts for their own children and nobody else's.

Deliberately *not* matched by ``/api/pricing/agreements/mine/``, which stays
student-only. A waitlist entry is scheduling; a negotiated rate is money, and
who in a family may read one is a separate decision (tech-debt.md).

- **Auth**: Required
- **Tenant Scope**: Requires investigation
- **Roles**: Requires investigation
- **Parameters**:
  - organization_pk (path)
- **Status Codes**: 200

