# Phase 9 — Payments: Subscription Billing & Tuition Collection

> Supersedes earlier drafts of this spec. Previous versions assumed
> IqraLMS takes a transaction cut and disburses to teachers directly —
> neither is true of the actual business model. An earlier version of
> this draft also included percentage-of-revenue teacher pay; it was
> removed after review (see "Explicitly out of scope") because it leaks
> a family's individual pricing to their teacher, which nothing else in
> this codebase does.

## Goal

Two independent money flows, both real, both through Paystack, that must
never be confused with each other:

```text
9a. Academy owner → IqraLMS       Recurring monthly subscription.
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
- The business model is subscription-only. There is no per-transaction
  platform commission on tuition.
- Paystack has no OAuth-style "bring your own existing account" mechanism
  comparable to Stripe Connect Standard — checked directly against current
  documentation, not assumed from a Stripe-shaped mental model. The
  platform-creates-a-subaccount-under-its-own-key pattern (Subaccounts) is
  the real mechanism available, and it's what §9b uses.
- `organizations.Organization.is_active` already exists and its own
  docstring says nothing reads it yet. This phase is the first thing that
  does.
- Teacher compensation is still hourly-only (`OrganizationTeacherConfiguration
  .hourly_payout_rate`), a deliberate Phase 8 decision, not an oversight —
  extending it to per-class and fixed amounts is still worth doing even
  though this phase will not execute any teacher payment.

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
model with no structural change. `fixed_period` does not — it has no
booking to attach to, and needs its own period-driven generator,
parallel to and independent of the booking-driven one.

With `percentage` gone, all three remaining types share one property
cleanly, with no exception: **none of them can be derived from, or reveal,
what any individual family actually pays.** A teacher's pay is the same
for every student at a given rate, full stop — see Data integrity.

### 2. Subscription billing (owner → IqraLMS)

Paystack Plans + Subscriptions — a different Paystack product from
everything else in this spec, because this is the one flow where IqraLMS
itself is the merchant being paid.

```text
PlatformSubscription
  organization:        OneToOne to organizations.Organization
  paystack_plan_code:   the one Plan IqraLMS defines (₦X/month)
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
use IqraLMS — one flag, one writer (this webhook handler), everything
else just reads it the way it presumably already reads active membership.

### 3. Tuition collection & routing (family/parent → academy)

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
   `percentage_charge`'s direction oppositely, which has to be resolved
   against the live API before this ships, not guessed at here.
2. Payer is redirected to `authorization_url`.
3. **`charge.success` is the only thing that moves a `FamilyPayment` to
   `paid`.** The redirect callback may call `transaction/verify` to show
   an immediate result, but does not itself finalize anything — same
   discipline Phase 8 already applies to `finalized`.
4. A webhook that can't match its reference to a `pending` row logs and
   exits. It does not create a record to make itself fit.

Because the split routes directly from Paystack to the academy's bank
account, the money never passes through an IqraLMS-held balance — not a
revenue decision, a custody one: the platform is never in a position of
holding tuition money it doesn't own, even for a moment.

## API surface

```text
POST /api/billing/subscribe/                (owner)
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
do not merge subscription events and tuition events onto one handler,
they're different Paystack products with different payloads.

## Data integrity

- a `FamilyPayment.amount` is snapshotted at initialization, never
  recalculated from a possibly-changed `PricingAgreement` afterward;
- every webhook request's signature is verified before its payload is
  trusted — true for both webhook endpoints, no exceptions;
- a webhook event is processed at most once — store and check Paystack's
  event id/reference before applying a transition, since Paystack
  explicitly documents retrying undelivered webhooks;
- `Organization.is_active` has exactly one writer: the subscription
  webhook handler;
- a `FamilyPayment` or `PlatformSubscription` row is organization-scoped
  like every other financial model already in this codebase;
- a teacher's compensation figures (§1) are never read by, and never
  written from, anything in §2 or §3. No exceptions — this is what
  dropping `percentage` buys: the boundary is absolute, not
  "absolute except one documented case."

## Financial calculation rules

- all tuition and subscription amounts in `NGN`, matching
  `payouts.PAYOUT_CURRENCY` — no conversion;
- money quantized to `0.01` with `ROUND_HALF_UP`, same point in the
  calculation every time, same convention `payouts` already set;
- who bears Paystack's transaction fee on a tuition payment — the family,
  or absorbed by the academy out of its settlement — is not decided by
  this spec; see "Stop and ask";
- who bears Paystack's fee on the subscription charge — IqraLMS absorbs
  it as a cost of the subscription price, or it's passed through — is a
  pricing decision, not a technical one; also not decided here.

## Period boundaries

A `fixed_period` teacher's cadence boundary reuses whatever period
definition `payouts` statements already use. Do not define "the start of
the month" a second way.

## Historical behaviour

A `paid` `FamilyPayment` is immutable, same discipline as
`TeacherPayout.IMMUTABLE_FIELDS`. A later `PricingAgreement` change never
retroactively edits it. A `failed` or `abandoned` payment is kept, not
deleted — it's still part of the family's real history.

## Relationship with pricing

§3 reads `PricingAgreement.agreed_rate` to know what a `FamilyPayment`
should be initialized for. `pricing` itself is unchanged — it still
charges nobody by itself; this phase is the thing that finally acts on
what it records. `pricing` is never read by §1's compensation
calculation, in either direction — see Data integrity.

## Relationship with payouts

Unchanged in execution. §1 extends what `OrganizationTeacherConfiguration`
can express, now in three ways instead of one; `payouts` still only
*computes and records* what's owed, the same way it does today. This
phase adds no disbursement step, on purpose — see "Explicitly out of
scope" for why, and for what was deliberately set aside rather than
merely deferred.

## Visibility

### Platform owner (you)
Sees `PlatformSubscription` status across every academy — this is your
own revenue. Full tuition-level financial oversight *across* academies is
explicitly a later phase (see "Explicitly out of scope"); this phase only
needs the data to exist in a shape that phase can later query.

### Academy Owner/Admin
Full visibility into their own academy's `FamilyPayment` history and
their own `PlatformSubscription` status. Configures compensation types
and rates. No visibility into another academy's data — enforced the same
way every other cross-tenant boundary already is.

### Lead Teacher
Same payout-statement visibility Phase 8 already grants. No subscription
or tuition visibility beyond what Phase 8 already exposes about payouts.

### Sub-teacher
Their own payout figures — a flat number per session or per period, never
tied to what any specific family paid. Nothing about family payments,
nothing about the academy's subscription.

### Student (adult)
Their own `FamilyPayment` history and outstanding balance. Can initiate a
payment for themself.

### Parent
Linked children's `FamilyPayment` history and outstanding balance. Can
initiate payment on a child's behalf. No visibility into any other
family's payments, and none into teacher compensation or the academy's
subscription.

### Minor student
No financial visibility, no payment action, anywhere — including the
simplified dashboard from `decisions.md` D-008. Unconditional, not a
default a later phase softens.

## Explicitly out of scope

### Why not percentage-based teacher pay

Drafted, then removed. The problem: if a teacher's pay is any percentage
of what a specific family actually paid, the teacher can divide their own
payout by their known percentage and recover that family's real rate —
and compare it against another student at the same level to learn who's
on a hardship or sibling discount. Phase 8's own payouts docstring is
explicit that a family's discount is "absorbed by the academy's margin,"
never visible to the teacher; percentage pay tied to actual payment
breaks that the first time it runs.

Basing the percentage on the level's *standard*, undiscounted rate
instead — so every student at a level pays the same teacher the same
amount regardless of their individual discount — would have closed that
leak without removing the feature. It was removed anyway, by product
decision, in favor of keeping all three remaining types uniformly simple
and auditable, with zero paths between family pricing and teacher pay
rather than one carefully-guarded path. If percentage-of-revenue pay is
needed later, it's a small, self-contained addition on top of this
phase — the standard-rate approach above is exactly how to build it
safely when that day comes.

### Everything else set aside

- **teacher disbursement via Paystack** — not deferred, rejected: a
  teacher is the academy's own relationship, not the platform's, the
  same way Brightwheel or Jackrabbit don't run a daycare's or a studio's
  payroll even though they process that business's customer payments;
- **Paystack Split Payments at the point of tuition charge** — not
  needed, there's no platform cut to split out;
- a platform-owner oversight dashboard — explicitly named as "something
  for later" in this spec's own design conversation; §9's job is to make
  sure the underlying data (`PlatformSubscription`, `FamilyPayment`,
  scoped per `Organization`) exists in a shape that dashboard can be
  built against, not to build it now;
- refunds or reversals of a `paid` `FamilyPayment`;
- currency conversion, tax handling, accounting exports;
- an owner/admin recording a payment made outside the app on a family's
  behalf;
- partial payments or installment plans spanning multiple pricing
  periods;
- automatic retry of a failed tuition payment beyond notifying the payer;
- a per-academy custom commission rate — the platform takes 0% of
  tuition, uniformly, by business-model decision, not configuration.

## Acceptance criteria

1. An academy owner can subscribe, and `Organization.is_active` turns
   `False` only after `subscription.disable`, never earlier.
2. `invoice.payment_failed` triggers a dunning action before
   `subscription.disable` ever fires.
3. An owner completes bank-details setup once; every subsequent family
   payment for that academy routes to that same account automatically.
4. A `charge.success` webhook, and only that webhook, moves a
   `FamilyPayment` to `paid`.
5. Re-delivery of the same webhook event does not double-apply a status
   change, on either webhook endpoint.
6. An adult student or a linked parent can pay; a minor cannot, anywhere.
7. `compensation_type` of `per_class` or `fixed_period` produces the
   correct payout figures without touching `hourly` teachers' numbers,
   and no compensation figure for any type can be derived from a specific
   family's payment.
8. No academy can see another academy's subscription status, tuition
   payments, or compensation configuration.
9. Full test suite and `python manage.py check` pass.

## Suggested implementation breakdown

### 9.1 Extend `OrganizationTeacherConfiguration`
Schema only — `compensation_type` (three values) and the two new rate
field groups.

### 9.2 Per-class and fixed-period payout calculation
Extend the existing calculation for `per_class`; add the separate
`fixed_period` generator. No Paystack involved yet.

### 9.3 `PlatformSubscription` and the subscription webhook
Plan creation (once, by you), subscribe flow, the six-event webhook
handler, `is_active` gating.

### 9.4 Subaccount onboarding
The bank-details form, Resolve Account confirmation step, Create
Subaccount call, `Organization.paystack_subaccount_code`.

### 9.5 `FamilyPayment` and the tuition webhook
`initialize`/`verify`, the `charge.success` handler. Confirm the real
`percentage_charge` direction against a test-mode transaction before
this is considered done.

### 9.6 Family and owner-facing statements
Read-only payment history per the Visibility rules above.

### 9.7 Frontend: subscription, bank setup, and payment entry points
Owner-side subscribe/bank-setup flows; adult-student and parent payment
entry points; explicitly nothing payment-related in the minor-student
dashboard.

## Manual testing checklist

- [ ] subscribe an academy, confirm `is_active` stays `True` through
      `invoice.create` and `invoice.update`;
- [ ] force a failed card charge, confirm `invoice.payment_failed` fires
      a dunning action and `is_active` does *not* flip yet;
- [ ] let a subscription actually lapse, confirm `subscription.disable`
      flips `is_active` and the academy loses access;
- [ ] complete bank-details setup for an academy, confirm the resolved
      account name matches before the subaccount is created;
- [ ] pay a tuition charge in test mode, confirm the academy's test bank
      account receives the full amount, not the platform's balance;
- [ ] replay a `charge.success` payload twice, confirm no double-apply;
- [ ] configure two students at the same level with different
      `PricingAgreement.agreed_rate` values (one standard, one
      discounted); confirm their teacher's payout for an equivalent
      session is identical either way;
- [ ] confirm a minor student has no payment entry point anywhere, by
      role, not just hidden in the UI;
- [ ] confirm academy A cannot see academy B's subscription status or
      tuition payments.

## Definition of done

Every acceptance criterion passes, the full test suite and
`python manage.py check` pass, and every item below has an explicit,
recorded answer in `decisions.md` before implementation starts.

## Stop and ask instead of guessing

- **Who bears the Paystack fee on tuition** — the family, or the academy
  out of its settlement?
- **Who bears the Paystack fee on the subscription** — absorbed into the
  subscription price, or passed through?
- **`percentage_charge` direction** — confirm against a real test-mode
  transaction; the two current Paystack doc versions found during this
  spec's research disagree.
- **Dunning grace period** — how long between `invoice.payment_failed`
  and treating the academy as at-risk in owner-facing UI, before
  `subscription.disable` eventually does the hard cutoff?
