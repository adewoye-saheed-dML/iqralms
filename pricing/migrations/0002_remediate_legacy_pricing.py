"""SaaS Phase 5: non-destructively remediate legacy pricing agreements.

Ensures all historical PricingAgreement participants (students and approvers)
hold active memberships in the owning academy, and deactivates older duplicate
active agreements so each (student, level) pair has at most one active agreement.
"""

from django.db import migrations

from pricing.legacy import remediate_legacy_pricing_migration


class Migration(migrations.Migration):

    dependencies = [
        ("pricing", "0001_initial"),
        ("organizations", "0001_initial"),
        ("curriculum", "0004_enforce_curriculum_tenancy"),
    ]

    operations = [
        migrations.RunPython(
            remediate_legacy_pricing_migration,
            migrations.RunPython.noop,
        ),
    ]
