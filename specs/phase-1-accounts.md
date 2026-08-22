# Phase 1 — Accounts

## Goal

Users can exist with a role, a timezone, and (for minors) a link to a parent
account. Nothing about teaching, booking, or payment yet — this phase is
purely the identity layer everything else will build on.

## Why this phase first

Every later phase (curriculum, scheduling, assessment, payouts) references a
`User`. Getting roles and parent-linking right now avoids reworking foreign
keys later.

## Data model

**User** (extends Django's `AbstractUser`)
- `role`: one of `lead`, `sub`, `student`, `parent`
- `timezone`: IANA string, e.g. `"Africa/Lagos"`. Required at signup.
- `date_of_birth`: nullable date
- `is_minor`: boolean, computed at signup from `date_of_birth` (under 18 = true)

**ParentLink**
- `parent`: FK to User (must have role=`parent`)
- `student`: FK to User (must have role=`student`)
- A student can have more than one parent linked. A parent can have more
  than one child linked.

**TeacherProfile** (one-to-one with User, only for role=`lead` or `sub`)
- `bio`: text, optional
- `max_weekly_hours`: positive integer — the capacity dial referenced in
  later phases. Required.
- `hourly_payout_rate`: decimal, nullable (lead teacher doesn't need one,
  they don't pay themselves)
- `is_lead`: boolean
- `approved`: boolean, defaults to False — a sub-teacher isn't bookable
  until this is flipped true (by a lead, done manually via admin for now)

## Rules to enforce

- A `student` account where `is_minor=True` cannot be fully active (able to
  book sessions — irrelevant yet, but don't let the account exist as
  "complete" state) until at least one `ParentLink` exists for it. For this
  phase, just enforce: minor signup returns a "pending parent link" status
  instead of a normal active account.
- `role=parent` accounts cannot have a `TeacherProfile` or be the `student`
  side of a `ParentLink`.
- `TeacherProfile.is_lead=True` should only ever exist for exactly one user
  in the system for now (single-academy assumption) — don't hard-enforce
  this in the DB yet, just don't build UI/API that allows creating a second
  one without me confirming that's intended.

## API surface (this phase only)

- `POST /api/auth/register/` — creates a User. If role=student and
  date_of_birth implies minor, response indicates a parent link is
  required before the account is usable.
- `POST /api/auth/login/` — token auth via dj-rest-auth, standard.
- `POST /api/accounts/parent-links/` — authenticated parent creates a link
  to a student account (by student email or a signup code — pick one and
  tell me which, don't silently decide, this is a UX call).
- `GET /api/accounts/me/` — returns the logged-in user's own profile
  including role, timezone, and (if a teacher) their TeacherProfile.

## Acceptance criteria (must be true before this phase is "done")

1. Migrations apply cleanly on a fresh DB.
2. A test creates one User of each role and asserts the role field sticks.
3. A test creates a minor student without a ParentLink and asserts the
   account is *not* returned as fully active.
4. A test links a parent to a student and asserts the parent can now see
   that student via `GET /api/accounts/me/` or an equivalent endpoint —
   we'll need a "my linked children" endpoint too, add it if missing.
5. A test attempts to give a `parent`-role user a TeacherProfile and
   asserts this is rejected.
6. `python manage.py check` and the full test suite pass.

## Explicitly out of scope for this phase

Curriculum, levels, bookings, video, payouts, pricing — none of it. Do not
add fields "while I'm in here" for future phases. If something later
clearly needs a field on User that isn't here, flag it, don't add it
silently.
