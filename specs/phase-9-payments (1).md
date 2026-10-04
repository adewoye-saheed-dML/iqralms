# Phase 9 — Payments: Subscription Billing & Tuition Collection

> Supersedes earlier drafts. Previous versions assumed a transaction cut
> and teacher disbursement — neither is true of the actual business
> model. A percentage-based teacher pay type was drafted and removed (see
> "Explicitly out of scope"). This version adds student-count-based
> subscription tiers, a hard student cap per tier, and display-only USD
> pricing for international visitors — all three decided in this spec's
> own design conversation, not assumed.

## Goal

Two independent money flows, both real, both through Paystack, that must
never be confused with each other:

```text
9a. Academy owner → IqraLMS       Recurring monthly subscription, priced
                                   by how many students the academy has.
                                   This is the platform's actual revenue.

9b. Family/parent → Academy       Tuition payment, routed straight to the
                                   academy's own bank account. IqraLMS
                                   takes 0% and never holds this money,
                                   even briefly.
```

Nowhere in this phase does IqraLMS disburse money to a teacher. A teacher
is the academy's own staff/contractor relationship, not the platform's —
confirmed by looking at how comparable products in this exact category
(Brightwheel, Jackrabbit Class, Procare, Mindbody) handle it: every one of
them processes tuition in-app, and none of them pay the business's own
staff on its behalf. `payouts.TeacherPayout` keeps doing exactly what it
already does — telling an owner or lead what they owe a teacher — and this
phase extends *how that's calculated*, never *who pays it*.

## Why this phase now

Confirmed across this spec's design conversation, not assumed:

- IqraLMS is multi-tenant: `organizations.Organization` is the tenant, and
  every academy is one row in it, already scoped the way every other
  financial model in this codebase is scoped (`in_organization()`).
- The business model is subscription-only, priced by student count. There
  is no per-transaction platform commission on tuition.
- Paystack has no OAuth-style "bring your own existing account" mechanism
  comparable to Stripe Connect Standard — checked directly against current
  documentation. The platform-creates-a-subaccount-under-its-own-key
  pattern (Subaccounts) is the real mechanism available, used in §9b.
- Paystack does support charging international cards on a standard
  Nigeria-based account today — the cardholder's own bank converts at
  checkout. *Settling* in USD is a separate thing requiring a Zenith Bank
  USD domiciliary account, a real banking step outside this codebase.
  This phase uses the first (already available); the second is not in
  scope — see §2 and "Stop and ask".
- `organizations.Organization.is_active` already exists and its own
  docstring says nothing reads it yet. This phase is the first thing that
  does.
- Teacher compensation is still hourly-only (`OrganizationTeacherConfiguration
  .hourly_payout_rate`), a deliberate Phase 8 decision, not an oversight —
  extending it to per-class and fixed amounts is still worth doing even
  though this phase will not execute any teacher payment.
- Today, `create-academy-form.tsx` creates a fully active, free
  organization with no payment step at all. This phase is what turns that
  into a gated flow — see §2's onboarding sequencing.

## Scope

### 1. Teacher compensation types

Extends `OrganizationTeacherConfiguration`:

```text
compensation_type: hourly | per_class | fixed_period
hourly_payout_rate:    Decimal, existing field, unchanged
per_class_rate:        Decimal, nullable
fixed_period_amount:   Decimal, nullable
fixed_period_cadence:  weekly | monthly, nullable
```

Three types, not four. A `percentage` type was drafted and removed — see
"Explicitly out of scope" for why.

All other Phase 8 rate rules carry over unchanged: the lead controls
these fields within their own academy; a change affects future
calculation only; a finalized `TeacherPayout` keeps the figures it was
computed with; students, parents and sub-teachers cannot touch any of
this; assessment scores never factor in.

`hourly` and `per_class` fit the existing session-driven `TeacherPayout`
model with no structural change. `fixed_period` does not — it needs its
own period-driven generator, parallel to and independent of the
booking-driven one.

None of these three types can be derived from, or reveal, what any
individual family actually pays — see Data integrity.

### 2. Subscription billing (owner → IqraLMS), by tier

Four tiers, three of them self-serve:

```text
Tier           Student cap   Price (NGN/mo)   Self-serve?
starter        25            15,000           yes
growth         100           35,000           yes
professional   250           75,000           yes
institutional  unlimited     custom           no — sales-assisted
```

These figures are a starting proposal from this spec's design
conversation, not a locked business decision — they're wired as
configuration (see below), not hardcoded into calculation logic, so
changing a number later doesn't mean changing code.

```text
SUBSCRIPTION_TIERS  # code-level constant, not a DB table — four fixed
                     # tiers don't need one, matching this codebase's
                     # existing bias against modeling something as a
                     # table when a constant says the same thing (see
                     # payouts' single hourly-rate-model decision)
  starter:       {student_cap: 25,  price_ngn: 15000, paystack_plan_code: <...>}
  growth:        {student_cap: 100, price_ngn: 35000, paystack_plan_code: <...>}
  professional:  {student_cap: 250, price_ngn: 75000, paystack_plan_code: <...>}
  institutional: {student_cap: None, price_ngn: None, paystack_plan_code: None}
```

`institutional` has no Paystack plan at all. An institutional
`PlatformSubscription` is created manually (by you, through Django admin
or a one-off management command), not through the `subscribe` endpoint —
the pricing page's existing "Contact Institutional Team" path stays
exactly as sales-assisted as it already is today, just now actually
provisioning a real subscription record instead of nothing.

```text
PlatformSubscription
  organization:        OneToOne to organizations.Organization
  tier:                 starter | growth | professional | institutional
  paystack_plan_code:   null for institutional
  paystack_customer_code: Paystack's customer record for the owner
  paystack_subscription_code:
  status:               active | past_due | not_renewing | disabled
  current_period_end:   datetime, from the subscription object
  created_at / updated_at
```

Lifecycle, driven entirely by webhooks (confirmed current event names,
not assumed):

- `subscription.create` → `status = active`
- `invoice.create` → informational; Paystack is about to attempt the next
  charge. Nothing to do but note it.
- `invoice.update` → the charge succeeded; refresh `current_period_end`.
- `invoice.payment_failed` → `status = past_due`. **Start dunning here**,
  not on `subscription.disable` — a common, named mistake in Paystack's
  own integration guidance is waiting too long to act.
- `subscription.not_renew` → `status = not_renewing`; the academy keeps
  access through `current_period_end`, no sooner.
- `subscription.disable` → `status = disabled`, and this is what flips
  `Organization.is_active = False`.

`Organization.is_active` is the single access gate. Nothing else in the
product should independently decide whether a lapsed academy can still
use IqraLMS — one flag, one writer (this webhook handler).

**Onboarding sequencing**: `create-academy-form.tsx` still creates the
`Organization` first, same as today, but it is not usable until a
`PlatformSubscription` reaches `active`. The frontend's onboarding wizard
enforces this by ordering — see `phase-9-payments-frontend.md` — but the
backend must not rely on the frontend's ordering alone: any endpoint that
lets an owner configure or use their academy (curriculum, teachers,
scheduling, etc.) should check `organization.is_active`, not just trust
that the frontend wizard was followed in order.

### 3. Student cap enforcement

One check, one place, called from everywhere a student can be added —
not reimplemented at each call site:

```text
can_add_student(organization) -> bool
  reads organization.platformsubscription.tier's student_cap
  (None/institutional = always True)
  compares against organization's current active student count
```

Called from every path that creates a student membership:

- invitation acceptance (a student or a parent-on-behalf-of-a-minor
  accepting an invite);
- CSV import (`imports` app) — see the open question below about a batch
  that would cross the cap mid-import;
- any other direct student-creation path that exists or is added later.

When blocked, the action fails with a clear, specific message — "this
academy has reached its student limit for the Starter plan" — not a
generic permission error. The owner should not have to guess why an
invitation silently failed.

This is enforcement at the point of the 26th student, which is also why
the frontend side of this (`phase-9-payments-frontend.md`) needs a
capacity indicator *before* that point — blocking with no warning is a
worse experience than blocking at all.

### 4. USD display pricing (not USD charging)

A new, public, unauthenticated endpoint:

```text
GET /api/billing/tiers/
```

Returns the four tiers with both currencies computed server-side:

```text
[{
  "tier": "starter",
  "student_cap": 25,
  "price_ngn": 15000,
  "price_usd_display": 20   # computed from a manually-set rate, not a
                             # live FX API — see below
}, ...]
```

`price_usd_display` comes from one manually-maintained setting (e.g.
`USD_DISPLAY_RATE`), not a live exchange-rate API call. This number is
purely informational — it exists so the marketing pricing page can show
"~$20/mo" to a visitor who isn't thinking in Naira. **Every actual
Paystack transaction this phase creates, for every tier, uses the NGN
amount, unconditionally** — there is no USD-denominated charge anywhere
in this phase. A live FX dependency would be real complexity in service
of a number nobody's money actually moves through; a manually-updated
setting is honest about what this number is for.

The marketing pricing page (`src/app/(marketing)/pricing/page.tsx`)
should fetch from this endpoint instead of the hardcoded tiers currently
in that file — see `phase-9-payments-frontend.md` for the rest of that
page's rework.

### 5. Tuition collection & routing (family/parent → academy)

```text
Organization
  + paystack_subaccount_code: nullable until the owner completes payment
    setup
```

Onboarding, once per academy, by the owner: a plain bank-details form
(bank name + account number) → backend calls Paystack's Resolve Account
Number endpoint → show the returned account name back to the owner for
confirmation ("Is this you?") → backend calls Create Subaccount under
IqraLMS's own secret key → store the returned `subaccount_code`. The
owner never sees a Paystack dashboard, API key, or URL at any point.

```text
FamilyPayment
  student:             FK to accounts.User (role student)
  organization:          FK, denormalized the same way payouts denormalizes
                         cohort — the tenancy rule becomes a DB constraint
  initiated_by:         FK to accounts.User — the student themself, or the
                         paying parent; never a minor (see Visibility)
  pricing_agreement:     FK to pricing.PricingAgreement, PROTECT
  amount:               Decimal, snapshotted from agreed_rate at
                         initialization
  currency:             'NGN'
  paystack_reference:    unique, server-generated
  status:               pending | paid | failed | abandoned
  raw_webhook_payload:   JSON
  created_at / paid_at
```

Lifecycle:

1. Backend creates a `pending` `FamilyPayment`, calls
   `transaction/initialize` with the organization's `subaccount_code` and
   a `percentage_charge` configured so the academy receives the full
   amount — the exact field value needs a real test-mode transaction to
   confirm, not assumption: two current Paystack doc versions describe
   `percentage_charge`'s direction oppositely.
2. Payer is redirected to `authorization_url`.
3. **`charge.success` is the only thing that moves a `FamilyPayment` to
   `paid`.** The redirect callback may call `transaction/verify` for an
   immediate read, but does not itself finalize anything.
4. A webhook that can't match its reference to a `pending` row logs and
   exits. It does not create a record to make itself fit.

Because the split routes directly from Paystack to the academy's bank
account, the money never passes through an IqraLMS-held balance.

## API surface

```text
GET  /api/billing/tiers/                     (public, no auth)
POST /api/billing/subscribe/                 (owner)
POST /api/billing/webhook/paystack/
GET  /api/billing/status/                    (owner — their own subscription)

POST /api/payments/subaccount/setup/         (owner — bank details, once)
POST /api/payments/initialize/
GET  /api/payments/verify/{reference}/
POST /api/payments/webhook/paystack/
GET  /api/payments/mine/                     (student)
GET  /api/payments/children/                 (parent)
GET  /api/payments/organization/             (owner/admin, lead)
```

Two separate webhook endpoints, two separate signature verifications —
do not merge subscription events and tuition events onto one handler.

## Data integrity

- a `FamilyPayment.amount` is snapshotted at initialization, never
  recalculated from a possibly-changed `PricingAgreement` afterward;
- every webhook request's signature is verified before its payload is
  trusted — true for both webhook endpoints, no exceptions;
- a webhook event is processed at most once — store and check Paystack's
  event id/reference before applying a transition;
- `Organization.is_active` has exactly one writer: the subscription
  webhook handler;
- `can_add_student()` is the only path that may create a student
  membership past the cap check — no call site re-implements the
  comparison itself;
- a `FamilyPayment` or `PlatformSubscription` row is organization-scoped
  like every other financial model already in this codebase;
- a teacher's compensation figures (§1) are never read by, and never
  written from, anything in §2–§5. No exceptions.

## Financial calculation rules

- all tuition and subscription amounts in `NGN`, matching
  `payouts.PAYOUT_CURRENCY`. `price_usd_display` is a display figure only
  and is never passed to Paystack as a charge currency;
- money quantized to `0.01` with `ROUND_HALF_UP`, same convention
  `payouts` already set;
- who bears Paystack's transaction fee on a tuition payment — the family,
  or absorbed by the academy out of its settlement — is not decided by
  this spec; see "Stop and ask";
- who bears Paystack's fee on the subscription charge is a pricing
  decision, not a technical one; also not decided here.

## Period boundaries

A `fixed_period` teacher's cadence boundary reuses whatever period
definition `payouts` statements already use.

## Historical behaviour

A `paid` `FamilyPayment` is immutable, same discipline as
`TeacherPayout.IMMUTABLE_FIELDS`. A tier change or price change never
retroactively edits a past subscription charge. A `failed` or
`abandoned` payment is kept, not deleted.

## Relationship with pricing

§5 reads `PricingAgreement.agreed_rate` to know what a `FamilyPayment`
should be initialized for. `pricing` itself is unchanged — it still
charges nobody by itself. `pricing` is never read by §1's compensation
calculation, in either direction.

## Relationship with payouts

Unchanged in execution. §1 extends what `OrganizationTeacherConfiguration`
can express; `payouts` still only *computes and records* what's owed.
This phase adds no disbursement step, on purpose.

## Visibility

### Platform owner (you)
Sees `PlatformSubscription` status and tier across every academy — this
is your own revenue. Full tuition-level financial oversight *across*
academies is explicitly a later phase.

### Academy Owner/Admin
Full visibility into their own academy's tier, student-cap usage ("23 of
25 students"), `FamilyPayment` history, and `PlatformSubscription`
status. Configures teacher compensation types and rates. No visibility
into another academy's data.

### Lead Teacher
Same payout-statement visibility Phase 8 already grants. No subscription,
tier, or tuition visibility beyond what Phase 8 already exposes about
payouts.

### Sub-teacher
Their own payout figures only.

### Student (adult)
Their own `FamilyPayment` history and outstanding balance. Can initiate a
payment for themself.

### Parent
Linked children's `FamilyPayment` history and outstanding balance. Can
initiate payment on a child's behalf.

### Minor student
No financial visibility, no payment action, anywhere. Unconditional.

## Explicitly out of scope

### Why not percentage-based teacher pay

Drafted, then removed. The problem: if a teacher's pay is any percentage
of what a specific family actually paid, the teacher can divide their own
payout by their known percentage and recover that family's real rate —
and learn who's on a hardship or sibling discount. Phase 8's own payouts
docstring is explicit that a discount is "absorbed by the academy's
margin," never visible to the teacher.

Basing the percentage on the level's *standard*, undiscounted rate
instead would have closed that leak without removing the feature. It was
removed anyway, by product decision, in favor of keeping all three
remaining types uniformly simple, with zero paths between family pricing
and teacher pay. If needed later, the standard-rate approach is how to
build it safely.

### Why not true USD settlement, for now

Paystack does support it for Nigeria-based businesses, but it requires a
Zenith Bank USD domiciliary account — a real banking step outside this
codebase, not a configuration flag. Display-only conversion (§4) gets the
"$20 minimum" in front of international visitors without that dependency.
Revisit if/when that account exists.

### Everything else set aside

- **teacher disbursement via Paystack** — not deferred, rejected: a
  teacher is the academy's own relationship, not the platform's;
- **Paystack Split Payments at the point of tuition charge** — not
  needed, there's no platform cut to split out;
- a platform-owner oversight dashboard — explicitly later;
- refunds or reversals of a `paid` `FamilyPayment`;
- currency conversion for tuition itself, tax handling, accounting
  exports;
- an owner/admin recording a payment made outside the app on a family's
  behalf;
- partial payments or installment plans spanning multiple pricing
  periods;
- automatic retry of a failed tuition payment beyond notifying the payer;
- a per-academy custom commission rate on tuition — the platform takes 0%
  uniformly;
- soft/overage billing when a cap is hit — the decision made in this
  spec's design conversation is a hard block, not an automatic upgrade or
  extra charge. If that changes later, it's a different acceptance
  criterion than the one below.

## Acceptance criteria

1. An academy owner can subscribe to a self-serve tier, and
   `Organization.is_active` turns `False` only after
   `subscription.disable`, never earlier.
2. `invoice.payment_failed` triggers a dunning action before
   `subscription.disable` ever fires.
3. An owner completes bank-details setup once; every subsequent family
   payment for that academy routes to that same account automatically.
4. A `charge.success` webhook, and only that webhook, moves a
   `FamilyPayment` to `paid`.
5. Re-delivery of the same webhook event does not double-apply a status
   change, on either webhook endpoint.
6. An adult student or a linked parent can pay; a minor cannot, anywhere.
7. Attempting to add a 26th student to a Starter-tier academy is blocked
   with a specific, clear error, from every entry point that can create a
   student membership — invitation, CSV import, and any other.
8. `GET /api/billing/tiers/` returns all four tiers with correct NGN and
   display-USD figures, requires no authentication, and is never used as
   a charge currency source.
9. No academy can see another academy's subscription status, tuition
   payments, student count, or compensation configuration.
10. Full test suite and `python manage.py check` pass.

## Suggested implementation breakdown

### 9.1 Extend `OrganizationTeacherConfiguration`
Schema only — `compensation_type` (three values) and the two new rate
field groups.

### 9.2 Per-class and fixed-period payout calculation
Extend the existing calculation for `per_class`; add the separate
`fixed_period` generator. No Paystack involved yet.

### 9.3 Tiers constant and the public tiers endpoint
`SUBSCRIPTION_TIERS`, `GET /api/billing/tiers/`, `USD_DISPLAY_RATE`
setting. No subscription logic yet — this is pure configuration and can
be built and tested in isolation.

### 9.4 `PlatformSubscription` and the subscription webhook
Plan creation per self-serve tier (three Paystack Plans, done once, by
you), subscribe flow, the six-event webhook handler, `is_active` gating.

### 9.5 `can_add_student()` and its call sites
The single check function, wired into invitation acceptance and CSV
import. Confirm the batch-import open question (below) before this is
considered done.

### 9.6 Subaccount onboarding
The bank-details form, Resolve Account confirmation step, Create
Subaccount call, `Organization.paystack_subaccount_code`.

### 9.7 `FamilyPayment` and the tuition webhook
`initialize`/`verify`, the `charge.success` handler. Confirm the real
`percentage_charge` direction against a test-mode transaction.

### 9.8 Family and owner-facing statements
Read-only payment history, tier/capacity display, per the Visibility
rules above.

### 9.9 Frontend wiring
See `phase-9-payments-frontend.md` in full.

## Manual testing checklist

- [ ] subscribe an academy to each self-serve tier, confirm the correct
      Paystack Plan is charged for each;
- [ ] force a failed card charge, confirm `invoice.payment_failed` fires
      dunning and `is_active` does *not* flip yet;
- [ ] let a subscription actually lapse, confirm `subscription.disable`
      flips `is_active` and the academy loses access;
- [ ] add students up to a Starter academy's cap of 25, confirm the 26th
      — via invitation, and separately via CSV import — is blocked with
      a specific error;
- [ ] confirm `GET /api/billing/tiers/` requires no authentication and
      returns four tiers with both NGN and display-USD figures;
- [ ] complete bank-details setup for an academy, confirm the resolved
      account name matches before the subaccount is created;
- [ ] pay a tuition charge in test mode, confirm the academy's test bank
      account receives the full amount, not the platform's balance;
- [ ] replay a `charge.success` payload twice, confirm no double-apply;
- [ ] configure two students at the same level with different
      `PricingAgreement.agreed_rate` values; confirm their teacher's
      payout for an equivalent session is identical either way;
- [ ] confirm a minor student has no payment entry point anywhere;
- [ ] confirm academy A cannot see academy B's subscription, tier,
      student count, or tuition payments.

## Definition of done

Every acceptance criterion passes, the full test suite and
`python manage.py check` pass, and every item below has an explicit,
recorded answer in `decisions.md` before implementation starts.

## Stop and ask instead of guessing

- **CSV import crossing the cap mid-batch**: if an owner imports 50
  students at once on a 25-cap tier, does the whole batch reject, or does
  it import up to the cap and report the rest as failed rows? Not decided
  here.
- **Capacity-warning threshold**: the frontend should warn an owner
  before they hit the cap, not only block at it — at what remaining
  headroom (90% full? the last 3 spots?) does that warning first appear?
  See `phase-9-payments-frontend.md`.
- **Who bears the Paystack fee on tuition** — the family, or the academy
  out of its settlement?
- **Who bears the Paystack fee on the subscription** — absorbed into the
  listed tier price, or passed through?
- **`percentage_charge` direction** — confirm against a real test-mode
  transaction; the two current Paystack doc versions found during this
  spec's research disagree.
- **Dunning grace period** — how long between `invoice.payment_failed`
  and the hard `subscription.disable` cutoff?
- **Institutional provisioning process**: who on your side actually runs
  the manual `PlatformSubscription` creation for an institutional
  academy, and what does that runbook look like? Not decided here.
