# Phase 9 Remediation — IqraLMS Backend + Frontend

## Purpose

This document is the implementation instruction for completing and correcting **Phase 9 — Payments: Subscription Billing & Tuition Collection** across both repositories:

- Backend: `adewoye-saheed-dML/iqralms`
- Frontend: `adewoye-saheed-dML/iqralms_fe`

Work from the current `main` branches. Do not redesign unrelated parts of the application.

The objective is not to add another payment prototype. The objective is to make the existing Phase 9 implementation internally consistent with the product specification **and with Paystack's current API model**.

---

# 1. Current assessment

A substantial portion of Phase 9 already exists:

- teacher compensation types: hourly, per-class, fixed-period;
- subscription tiers and student caps;
- `PlatformSubscription`;
- `FamilyPayment`;
- Paystack transaction initialization;
- tuition webhook processing;
- payment history;
- owner billing page;
- student/parent payment page;
- minor restrictions;
- student-cap checks;
- automated backend/frontend tests.

However, Phase 9 is **not complete** because the current implementation has several integration-level problems.

### Critical problems to fix

1. The platform subscription flow calls Paystack `create_subscription` directly for a new customer instead of starting the first payment through `transaction/initialize` with the plan code.
2. The backend currently marks a platform subscription and academy active immediately after the subscription API call, before confirmed payment.
3. There are two Paystack webhook URLs. Paystack should deliver all relevant events to one configured webhook URL; the application should dispatch by event type.
4. The bank setup is implemented as Resolve Account + Create Subaccount in one request. The required UX is Resolve -> show returned account name -> owner confirms -> Create Subaccount.
5. Parent first-time tuition payment has an awkward manual PricingAgreement-ID fallback instead of reliably resolving the child's active agreement.
6. The marketing pricing page still contains stale fallback values that disagree with the backend tier definitions.
7. The existing tests are heavily mocked around Paystack. Passing mocks alone does not prove the actual Paystack Test Mode lifecycle.

Do not simply patch the tests to make them pass. Fix the underlying flows.

---

# 2. Paystack facts that the implementation must follow

Use the current Paystack behavior as the external integration source of truth.

## 2.1 First subscription payment

For a new customer, the recommended subscription path is:

```text
create Paystack Plan
        |
        v
initialize transaction with plan_code
        |
        v
Paystack checkout
        |
        v
customer pays
        |
        v
Paystack automatically subscribes customer to plan
```

Paystack's Create Subscription API expects the customer to already have an authorization from an earlier transaction. Therefore, do not use `create_subscription` as the initial checkout flow for a brand-new academy owner.

Reference: Paystack Subscriptions documentation:
https://paystack.com/docs/payments/subscriptions/

## 2.2 Webhooks

Configure **one Paystack webhook URL** for the integration/environment.

The backend should use one HTTP endpoint and dispatch events internally:

```text
POST /api/webhooks/paystack/
             |
             +-- charge.success
             +-- charge.failed
             +-- subscription.create
             +-- invoice.create
             +-- invoice.update
             +-- invoice.payment_failed
             +-- subscription.not_renew
             +-- subscription.disable
```

Keep the tuition-payment service and platform-billing service logically separate. Only the HTTP entry point is unified.

Reference:
https://paystack.com/docs/payments/webhooks/

## 2.3 Tuition routing to academy subaccount

For tuition:

- create a Paystack subaccount for the academy;
- initialize the transaction with that `subaccount`;
- use the Paystack split configuration that gives the main account 0% and the subaccount the remainder;
- use `bearer: "subaccount"` when the academy is intended to bear Paystack's transaction fee.

Current Paystack subaccount documentation states that `percentage_charge` is the percentage the **main account receives**, so `0` means the main account gets 0% and the subaccount receives the remainder.

References:
https://paystack.com/docs/api/subaccount/
https://paystack.com/docs/payments/split-payments/

## 2.4 Test Mode

Use Paystack Test Mode, not a separate Paystack Payment Page, to validate the application flow.

Current reusable successful test card:

```text
Card:   4084 0840 8408 4081
Expiry: any future date
CVV:    408
```

Paystack also provides failed test cards for negative tests.

Reference:
https://paystack.com/docs/payments/test-payments/

---

# 3. Target architecture

## 3.1 Two money flows remain separate

### A. Platform subscription

```text
Academy Owner
    |
    v
IqraLMS backend
    |
    v
Paystack transaction/initialize + plan_code
    |
    v
Paystack Checkout
    |
    v
successful first payment
    |
    v
subscription.create webhook
    |
    v
PlatformSubscription = active
Organization.is_active = True
```

### B. Tuition payment

```text
Adult Student / Parent
    |
    v
IqraLMS backend
    |
    v
FamilyPayment = pending
    |
    v
Paystack transaction/initialize
(subaccount = academy)
    |
    v
Paystack Checkout
    |
    v
charge.success webhook
    |
    v
FamilyPayment = paid
```

Never mix these two models.

---

# 4. BACKEND implementation tasks

Repository:

`adewoye-saheed-dML/iqralms`

## 4.1 Refactor Paystack client

Primary file:

`payments/paystack.py`

Keep:

- HMAC-SHA512 signature verification;
- transaction initialization;
- transaction verification;
- customer support where genuinely needed;
- subaccount resolution/creation.

Add or preserve a clean transaction initializer that supports:

```python
plan_code: Optional[str]
```

The JSON sent to Paystack for a platform subscription should contain the plan code instead of requiring a pre-existing customer authorization.

Do not remove tuition support for:

- `subaccount`;
- `bearer`;
- `metadata`;
- callback URL.

---

## 4.2 Correct the platform subscription service

Primary file:

`billing/services.py`

Current behavior that must be removed:

```text
create_customer()
    ->
create_subscription()
    ->
PlatformSubscription(status=active)
    ->
Organization.is_active=True
```

That is wrong for first-time checkout.

Replace it with a two-stage model.

### Stage 1 — initialize the first subscription payment

Create a service such as:

```python
initialize_platform_subscription(...)
```

Responsibilities:

1. authenticate that the caller is the organization owner;
2. validate the requested tier;
3. resolve the real Paystack plan code;
4. create a Paystack transaction reference generated by the backend;
5. call `transaction/initialize` with:
   - owner's email;
   - the real NGN plan amount as defined by the Paystack plan;
   - the `plan_code`;
   - a callback URL;
   - metadata identifying:
     - organization ID;
     - tier;
     - local reference/type.
6. store enough local pending state to safely identify the intended subscription;
7. return `authorization_url`, `access_code`, and `reference` to the frontend.

Do **not** mark the local subscription active here.

The actual Paystack plan amount is the billing authority for the first subscription charge. Do not pass the display-only USD value.

---

## 4.3 Decide how to store pending subscription state

The local database needs a reliable link between the first payment and the academy.

Preferred approach:

Extend `PlatformSubscription` with a nullable pending reference field, for example:

```text
paystack_initialization_reference
```

or introduce a dedicated small model if that is cleaner.

Requirements:

- pending state must be associated with exactly one organization;
- the first transaction reference must be unique;
- paid subscription state must remain auditable;
- later webhook events must resolve the subscription by Paystack subscription code/customer code/reference without guessing.

Do not create duplicate subscriptions when the owner clicks Subscribe more than once.

A repeated request while a valid pending initialization exists should either reuse the pending transaction context or safely create a new pending initialization without corrupting active state.

---

## 4.4 Make webhooks the authority for platform activation

Primary files:

`billing/services.py`
`billing/views.py`

Do not set:

```python
subscription.status = ACTIVE
organization.is_active = True
```

merely because Paystack initialization succeeded.

Correct state transitions:

```text
first checkout created
    -> local pending state

subscription.create
    -> PlatformSubscription.status = ACTIVE
    -> save Paystack subscription code
    -> save Paystack customer code/token where available
    -> Organization.is_active = True

invoice.create
    -> informational only

invoice.update
    -> refresh current_period_end
    -> recover from past_due when appropriate

invoice.payment_failed
    -> PlatformSubscription.status = PAST_DUE
    -> Organization remains active

subscription.not_renew
    -> PlatformSubscription.status = NOT_RENEWING
    -> Organization remains active until period end

subscription.disable
    -> PlatformSubscription.status = DISABLED
    -> Organization.is_active = False
```

The invariant is:

> A successful API initialization is not payment confirmation.

---

## 4.5 Add one Paystack webhook entry point

Create one URL such as:

```text
POST /api/webhooks/paystack/
```

or:

```text
POST /api/paystack/webhook/
```

Choose one and use it consistently.

The endpoint must:

1. read the raw request body;
2. read `X-Paystack-Signature`;
3. verify HMAC-SHA512 before trusting the payload;
4. parse JSON;
5. inspect `event`;
6. dispatch to:
   - tuition processor for tuition-related events;
   - billing processor for subscription/invoice events;
7. return HTTP 200 quickly after successful processing/acknowledgement.

The event dispatcher should be explicit. Do not silently send every event through both processors.

Recommended mapping:

```text
charge.success
charge.failed
    -> tuition processor

subscription.create
subscription.not_renew
subscription.disable
invoice.create
invoice.update
invoice.payment_failed
    -> billing processor
```

Unknown/unhandled events may be recorded as acknowledged events if that is consistent with the application's audit policy.

---

## 4.6 Preserve webhook idempotency

Keep the existing separate event ledgers if they are useful:

- `PaymentWebhookEvent`
- `BillingWebhookEvent`

But the unified endpoint must still guarantee at-most-once application.

The same webhook event must not cause a second state transition.

Tests must cover:

```text
same event ID twice
same event payload twice
unknown reference
unknown event type
invalid signature
```

---

# 5. BACKEND bank-subaccount onboarding

Current files:

`payments/views.py`
`payments/services.py`
`payments/paystack.py`
`organizations/models.py`

## Required behavior

Split the current single "setup" action into two backend operations.

### Operation 1 — resolve only

```text
POST /api/payments/subaccount/resolve/
```

Request:

```json
{
  "organization_id": 123,
  "bank_code": "057",
  "account_number": "0000000000"
}
```

Return:

```json
{
  "account_name": "Resolved Account Name",
  "bank_code": "057",
  "account_number": "0000000000"
}
```

Requirements:

- owner/admin authorization as defined by the spec;
- call Paystack Resolve Account Number;
- do not create a subaccount yet;
- do not persist the final subaccount code;
- return the resolved name.

### Operation 2 — confirm and create

```text
POST /api/payments/subaccount/confirm/
```

Request contains the same bank details, plus the owner's confirmation.

The backend should:

1. resolve again from the trusted bank/account input;
2. confirm the account details are still valid;
3. create the Paystack subaccount using `percentage_charge = 0`;
4. store `Organization.paystack_subaccount_code`;
5. return the confirmed account + subaccount code.

Do not trust an account name sent back from the browser as proof.

The browser confirmation is a UX step. The backend must re-resolve before creating the subaccount.

---

# 6. BACKEND tuition payment improvements

Primary files:

`payments/services.py`
`payments/views.py`
`payments/serializers.py`

## 6.1 Keep amount snapshot

When initializing a family payment:

```text
FamilyPayment.amount = PricingAgreement.agreed_rate
```

Store that value once.

Never recalculate a paid payment from the current PricingAgreement.

---

## 6.2 Do not mark paid from verify endpoint

Keep this invariant:

```text
GET /api/payments/verify/<reference>/
```

is informational.

It may call Paystack's verification API and return Paystack's current state, but it must not independently change:

```text
FamilyPayment.status
```

Only `charge.success` does that.

---

## 6.3 Improve parent agreement discovery

Current frontend behavior falls back to manual entry of a PricingAgreement ID for a first payment.

Replace that dependency.

Add a parent-safe backend endpoint or extend the family API to return the child's active tuition agreement, for example:

```text
GET /api/pricing/children/<student_id>/active/
```

or another route consistent with the existing pricing architecture.

Requirements:

- only a linked parent may retrieve it;
- it must be scoped to the linked child;
- it must return the active agreement only;
- never expose the private pricing `notes`;
- the backend payment initializer must still enforce the final relationship.

The frontend should never require the parent to know an internal database primary key just to make a first payment.

---

# 7. BACKEND student-cap enforcement

Current implementation is mostly correct.

Keep one source of truth:

```python
can_add_student(...)
```

Make sure it is invoked by every path that creates an active student membership.

At minimum verify:

- invitation acceptance;
- CSV import;
- direct student-member creation path;
- any remaining enrollment path that can create a student membership.

The current CSV logic validates capacity using cumulative new rows. Preserve that behavior.

Do not silently raise the cap in code.

---

# 8. BACKEND tier definitions

Primary file:

`billing/constants.py`

The canonical values are:

```text
starter:
    25 students
    ₦15,000/month

growth:
    100 students
    ₦35,000/month

professional:
    250 students
    ₦75,000/month

institutional:
    unlimited
    custom / sales-assisted
```

Keep one authoritative set of values.

The public endpoint:

```text
GET /api/billing/tiers/
```

must return those values plus display-only USD.

USD is never a charge currency.

---

# 9. FRONTEND implementation tasks

Repository:

`adewoye-saheed-dML/iqralms_fe`

## 9.1 Correct the owner subscription card

Primary file:

`src/features/billing/components/subscription-status-card.tsx`

Current behavior:

```text
click Subscribe
    ->
billingApi.subscribe()
    ->
assumes active subscription
```

Replace with:

```text
click Subscribe
    ->
POST initialize platform subscription
    ->
receive authorization_url
    ->
window.location.href = authorization_url
```

No subscription becomes visually "Active" merely because the initialization request succeeded.

---

## 9.2 Add a platform-subscription callback page

Create:

```text
src/app/app/billing/callback/page.tsx
```

and a callback component, for example:

```text
src/features/billing/components/subscription-callback-view.tsx
```

Expected callback:

```text
/app/billing/callback?reference=...
```

The callback page should:

1. read `reference` / `trxref`;
2. show a neutral "Confirming subscription..." state initially;
3. optionally call the backend verification/status endpoint for immediate feedback;
4. poll the local subscription status for a brief period;
5. show "Active" only after the backend reports the active state;
6. never claim success solely because Paystack redirected the browser;
7. provide a link back to `/app/billing`.

This mirrors the tuition callback principle.

---

## 9.3 Update the billing API client

Primary file:

`src/features/billing/api/billing.ts`

The subscription API should return something like:

```ts
interface SubscriptionInitialization {
  reference: string;
  authorization_url: string;
  access_code: string;
}
```

The method should be named to reflect what it actually does, for example:

```ts
initializeSubscription(...)
```

rather than pretending the request itself completes the subscription.

Keep:

```ts
getSubscriptionStatus(...)
```

as the source for the current local lifecycle state.

---

# 10. FRONTEND bank setup

Primary file:

`src/features/billing/components/bank-details-form.tsx`

Replace the current single-submit behavior with a visible two-step UI.

## Step 1

Fields:

- Bank
- Account number
- Business/settlement name if still required by product design

Button:

```text
Verify Bank Account
```

Call resolve endpoint.

Show:

```text
Account name: ...
Account number: ...
Bank: ...
```

Then:

```text
Is this the correct account?
```

Buttons:

```text
Back
Confirm & Activate Settlement
```

## Step 2

Call confirm endpoint.

After success:

```text
Bank Settlement Subaccount Active
```

Do not display a success state before the confirm call succeeds.

---

# 11. FRONTEND payment flow

Current tuition payment redirect is conceptually correct.

Keep:

```ts
window.location.href = data.authorization_url;
```

Do not use:

```ts
window.open(...)
```

Keep the callback behavior:

```text
Paystack redirect
    ->
callback page
    ->
backend verification/status polling
    ->
payment ledger confirms state
```

Do not turn the callback into a "success page" that ignores the local ledger.

---

# 12. FRONTEND parent payment flow

Primary files:

`src/features/payments/components/payments-panel.tsx`
`src/features/payments/components/payment-initiate-card.tsx`

Change the parent flow so that:

```text
Parent
    ->
select child
    ->
frontend loads child's active PricingAgreement
    ->
display amount
    ->
Pay Tuition
```

Remove the manual "enter Pricing Agreement ID" flow from the normal UX.

Keep a backend authorization check regardless of what the frontend sends.

---

# 13. FRONTEND capacity warnings

Primary file:

`src/features/billing/components/capacity-warning-banner.tsx`

Keep the existing warning component.

Wire it into the most useful owner-facing places, especially:

- owner/admin dashboard;
- student-management area;
- student-add/enrollment form;
- billing page.

Use the backend values:

```text
active_students
student_cap
remaining_capacity
capacity_warning
can_add_student
tier
```

Do not duplicate cap calculations in the frontend.

The frontend only displays backend capacity information.

---

# 14. FRONTEND pricing page — remove stale fallback values

Primary file:

`src/app/(marketing)/pricing/page.tsx`

The current fallback values disagree with the canonical backend values.

Remove or correct the stale fallback:

Current stale values must not survive:

```text
starter: ₦35,000 / 25
growth: ₦85,000 / 75
professional: ₦180,000 / 200
```

Canonical values:

```text
starter: ₦15,000 / 25
growth: ₦35,000 / 100
professional: ₦75,000 / 250
institutional: custom / unlimited
```

Prefer removing the fallback completely when practical, so the page has one source of truth.

If a build-safe fallback is absolutely required, it must exactly mirror `billing/constants.py` and must be clearly treated as a temporary resilience fallback.

---

# 15. FRONTEND role behavior

Preserve these rules:

### Owner/Admin

Can access:

```text
/app/billing
```

Can:

- manage platform subscription;
- configure settlement bank account;
- view academy tuition payment log.

### Lead Teacher

Must not access:

```text
/app/billing
/app/payments
```

### Ordinary Teacher

Must not access:

```text
/app/billing
/app/payments
```

### Adult Student

Can access:

```text
/app/payments
```

Can pay self.

### Parent

Can access:

```text
/app/payments
```

Can pay linked children.

### Minor Student

Must not see:

- Payments navigation item;
- payment page;
- payment button;
- billing information;
- payment history;
- tuition amount;
- financial calls where the UI could expose them.

Keep the backend restriction as authoritative. Frontend gating is not a security boundary.

---

# 16. API surface after remediation

Prefer the following consolidated API shape.

## Platform billing

```text
GET  /api/billing/tiers/
POST /api/billing/subscribe/initialize/
GET  /api/billing/status/
POST /api/webhooks/paystack/
```

Optional if useful:

```text
GET /api/billing/subscribe/verify/<reference>/
```

but this should remain informational and local-state aware.

## Tuition

```text
POST /api/payments/subaccount/resolve/
POST /api/payments/subaccount/confirm/
POST /api/payments/initialize/
GET  /api/payments/verify/<reference>/
GET  /api/payments/mine/
GET  /api/payments/children/
GET  /api/payments/organization/
```

Do not maintain two independent Paystack webhook URLs.

---

# 17. Migration requirements

Any schema changes must include real Django migrations.

After implementing the backend changes:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py check
```

Do not edit existing migration history destructively.

---

# 18. Backend tests to add/update

Backend test files:

```text
billing/tests/test_billing.py
payments/tests/test_payments.py
```

## Subscription tests

Must prove:

1. owner can request subscription initialization;
2. initialization returns an authorization URL;
3. initialization does not activate the organization;
4. initialization does not mark the subscription paid;
5. `subscription.create` activates subscription;
6. `subscription.create` activates organization;
7. `invoice.payment_failed` makes status `past_due`;
8. `invoice.payment_failed` does not deactivate the organization;
9. `subscription.not_renew` does not deactivate the organization;
10. `subscription.disable` deactivates the organization;
11. webhook replay is idempotent;
12. invalid webhook signature is rejected;
13. cross-tenant subscription status is denied.

Do not mock away the local state transition you are trying to prove.

---

## Tuition tests

Must prove:

1. adult student can initialize;
2. linked parent can initialize;
3. unlinked parent is denied;
4. minor cannot initialize;
5. subaccount resolve does not create subaccount;
6. subaccount confirm creates/stores the subaccount;
7. payment remains pending after initialization;
8. verification does not mark paid;
9. `charge.success` marks paid;
10. `charge.failed` marks failed where appropriate;
11. replay is idempotent;
12. unknown reference does not create a new payment;
13. paid payment remains immutable;
14. cross-tenant payment access is denied.

---

## Webhook dispatcher tests

Add tests for:

```text
charge.success -> tuition processor
charge.failed -> tuition processor

subscription.create -> billing processor
invoice.create -> billing processor
invoice.update -> billing processor
invoice.payment_failed -> billing processor
subscription.not_renew -> billing processor
subscription.disable -> billing processor
```

Also test:

```text
invalid signature -> 400
valid signature + unknown event -> acknowledged safely
same event ID twice -> processed once
```

---

# 19. Frontend tests to add/update

Test files:

```text
src/features/billing/__tests__/billing.test.tsx
src/features/payments/__tests__/payments.test.tsx
```

## Subscription UI

Prove:

- Subscribe button requests initialization;
- authorization URL causes full-page redirect;
- no active status is displayed before the backend says active;
- callback page displays "Confirming..." initially;
- callback page changes to active only after local state confirms it;
- owner can return to billing.

## Bank UI

Prove:

- Resolve step calls resolve API;
- resolved account name is shown;
- confirmation step is visible;
- confirmation calls confirm API;
- success appears only after confirmation succeeds.

## Parent payment UI

Prove:

- linked children appear;
- active tuition agreement is loaded automatically;
- tuition amount is displayed;
- parent can initiate payment without typing a PricingAgreement primary key;
- unlinked child cannot be selected through a trusted API path.

## Minor UI

Prove:

- minor sees no payment button;
- minor sees no payment navigation item;
- minor cannot reach the payment page through direct route access.

---

# 20. Integration testing with Paystack Test Mode

After code changes are complete, use a real deployed/staging backend with HTTPS.

## Step 1 — create Paystack Test Mode plans

Create these plans in Paystack Test Mode:

```text
Starter       ₦15,000 monthly
Growth        ₦35,000 monthly
Professional  ₦75,000 monthly
```

Copy the real plan codes into backend environment variables:

```env
PAYSTACK_STARTER_PLAN_CODE=PLN_...
PAYSTACK_GROWTH_PLAN_CODE=PLN_...
PAYSTACK_PROFESSIONAL_PLAN_CODE=PLN_...
```

Do not use placeholder plan codes.

---

## Step 2 — configure one webhook

Configure Paystack Test Mode to use:

```text
https://<backend-domain>/api/webhooks/paystack/
```

Do not configure separate tuition and billing webhook URLs.

---

## Step 3 — test platform subscription

As academy owner:

```text
/app/billing
    ->
Subscribe Academy
```

Expected:

```text
IqraLMS initializes transaction
    ->
Paystack checkout opens
```

Use the Paystack Test Mode successful card:

```text
4084 0840 8408 4081
CVV 408
expiry any future date
```

After payment:

```text
Paystack webhook
    ->
subscription.create
    ->
PlatformSubscription active
    ->
Organization.is_active = True
```

Verify all four layers:

1. Paystack transaction succeeded.
2. Paystack subscription exists.
3. local `PlatformSubscription` is active.
4. organization is active.

---

## Step 4 — test tuition

As owner:

```text
/app/billing
    ->
verify bank
    ->
confirm settlement account
```

Then as adult student or linked parent:

```text
/app/payments
    ->
Pay Tuition
    ->
Paystack checkout
    ->
successful test card
```

Verify:

```text
FamilyPayment initially pending
        ->
charge.success
        ->
FamilyPayment paid
```

Verify the payment was initialized with the academy's stored subaccount.

Verify the academy's Paystack settlement information rather than only trusting the local UI.

---

# 21. Do not use a Paystack Payment Page as the primary integration test

A standalone Paystack Payment Page bypasses important IqraLMS logic.

It does not prove:

- a `FamilyPayment` was created;
- the correct PricingAgreement was used;
- the correct organization was selected;
- the correct Paystack subaccount was used;
- the webhook matched the generated reference;
- the local payment ledger was updated.

The primary test must start from the IqraLMS UI/API.

---

# 22. Required final verification

Backend:

```bash
python manage.py check
python manage.py test
```

Frontend:

```bash
pnpm run typecheck
pnpm run lint
pnpm run test
```

Then perform the manual Paystack Test Mode flows.

Do not report Phase 9 complete until:

- subscription checkout works with a real Test Mode plan;
- subscription activation happens from webhook state;
- one webhook endpoint handles both billing and tuition events;
- bank onboarding is Resolve -> Confirm -> Create Subaccount;
- tuition checkout works end-to-end;
- parent first payment works without entering an internal PricingAgreement ID;
- minor financial restrictions work;
- student cap enforcement works;
- no stale pricing constants remain;
- all automated tests and `manage.py check` pass.

---

# 23. Definition of done

Phase 9 is complete only when the following end-to-end flows work.

## Flow A — platform subscription

```text
Owner
 -> /app/billing
 -> Select tier
 -> Paystack Checkout
 -> successful Test Mode payment
 -> subscription.create webhook
 -> local PlatformSubscription active
 -> Organization.is_active = True
 -> billing page shows Active
```

## Flow B — academy tuition setup

```text
Owner
 -> /app/billing
 -> enter bank account
 -> Resolve
 -> see account name
 -> Confirm
 -> Paystack subaccount created
 -> subaccount code stored
```

## Flow C — adult tuition

```text
Adult Student
 -> /app/payments
 -> active tuition agreement
 -> Pay Tuition
 -> Paystack Checkout
 -> charge.success webhook
 -> FamilyPayment paid
 -> ledger shows Paid
```

## Flow D — parent tuition

```text
Parent
 -> /app/payments
 -> select linked child
 -> child's active agreement loads
 -> Pay Tuition
 -> Paystack Checkout
 -> charge.success
 -> child's ledger shows Paid
```

## Flow E — failed/lapsed platform billing

```text
renewal fails
 -> invoice.payment_failed
 -> subscription = past_due
 -> organization remains active

subscription disabled
 -> subscription = disabled
 -> organization = inactive
```

---

# 24. Important implementation discipline

Do not:

- create a new percentage-based teacher-pay feature;
- reintroduce a transaction commission;
- make USD a charging currency;
- mark payments paid on browser redirect alone;
- mark subscriptions active on initialization alone;
- create two Paystack webhook URLs;
- trust bank/account confirmation data from the browser without re-resolving;
- expose private PricingAgreement notes;
- let frontend calculations replace backend authorization;
- hardcode new plan prices in multiple locations.

Do:

- preserve the existing tenant model;
- preserve organization isolation;
- preserve webhook idempotency;
- keep all money values in NGN;
- keep display USD informational only;
- make the smallest set of changes required to complete Phase 9 properly;
- update tests to prove behavior rather than merely mocking successful Paystack responses.

---

# 25. References used for the Paystack integration decisions

Paystack — Subscriptions:
https://paystack.com/docs/payments/subscriptions/

Paystack — Webhooks:
https://paystack.com/docs/payments/webhooks/

Paystack — Subaccounts:
https://paystack.com/docs/api/subaccount/

Paystack — Split Payments:
https://paystack.com/docs/payments/split-payments/

Paystack — Test Payments:
https://paystack.com/docs/payments/test-payments/
