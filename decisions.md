# Architectural & Product Decisions

## D-008: Minor Student Financial Invisibility
- **Decision**: Minor students have no financial visibility and no payment actions anywhere across the application, including dashboards.
- **Rationale**: Financial transactions and tuition liabilities belong strictly to paying parents/guardians or adult students.

## D-009: Tuition Paystack Fee Bearer
- **Decision**: The academy absorbs Paystack's transaction fee out of its settlement (`bearer: "subaccount"`).
- **Rationale**: Simplifies family pricing agreements so parents/students pay the exact advertised/agreed rate without variable transaction surcharges added at checkout.

## D-010: Subscription Paystack Fee Bearer
- **Decision**: IqraLMS absorbs Paystack fees as a cost of the platform subscription price.
- **Rationale**: Standard SaaS pricing model; academy owners pay flat advertised subscription price.

## D-011: Subaccount Platform Commission / Percentage Charge
- **Decision**: Platform commission is 0% (`percentage_charge: 0.0`).
- **Rationale**: IqraLMS operates strictly on subscription revenue and takes 0% cut of academy tuition. 100% of net tuition routes directly to the academy's bank account via Paystack subaccounts.

## D-012: Dunning Workflow and Grace Period
- **Decision**: When `invoice.payment_failed` is received, `PlatformSubscription.status` transitions to `past_due`. An immediate warning/at-risk banner is displayed to the academy owner. Organization access remains active (`Organization.is_active = True`) throughout Paystack's retry cycle until Paystack issues `subscription.disable`, which is the sole trigger that sets `Organization.is_active = False`.
- **Rationale**: Protects academy operations from abrupt cutoffs due to transient card failures while immediately warning the owner to update payment methods before disablement.
