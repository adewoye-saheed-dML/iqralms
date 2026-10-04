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

## D-013: CSV Import Student Cap Enforcement
- **Decision**: In CSV bulk student imports, valid rows are imported up to the tier's student cap. Any row that would cause the active student count to exceed the cap is marked as failed with the error message: "This academy has reached its student limit for the <Tier> plan." The whole batch is not rejected; existing headroom is filled.
- **Rationale**: Provides the best user experience by importing as many students as allowed while clearly explaining why subsequent rows were rejected.

## D-014: Capacity Warning Threshold
- **Decision**: Capacity warning triggers when active student count reaches 90% of the tier limit or when 3 or fewer spots remain (`active_students >= cap * 0.9` or `cap - active_students <= 3`).
- **Rationale**: Gives academy owners advance notice to upgrade to a higher tier before hitting hard blocks on invitations or imports.

## D-015: Institutional Subscription Provisioning Runbook
- **Decision**: Institutional subscriptions are provisioned manually by platform staff via Django admin or the `create_institutional_subscription` management command with `tier='institutional'`, `student_cap=None`, and `paystack_plan_code=None`.
- **Rationale**: Institutional sales involve custom commercial agreements and invoicing outside automated self-serve Paystack checkout.

