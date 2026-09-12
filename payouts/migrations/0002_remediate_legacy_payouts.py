"""SaaS Phase 7: non-destructively remediate legacy payout data.

Ensures all historical TeacherPayout participants (teachers) hold active memberships
in the owning academy, configures teacher roles, and aligns cohort references.
"""

from django.db import migrations

from payouts.legacy import remediate_legacy_payouts_migration


class Migration(migrations.Migration):

    dependencies = [
        ("payouts", "0001_initial"),
        ("organizations", "0001_initial"),
        ("accounts", "0003_organizationteacherconfiguration"),
        ("curriculum", "0004_enforce_curriculum_tenancy"),
        ("scheduling", "0008_enforce_availability_tenancy"),
    ]

    operations = [
        migrations.RunPython(
            remediate_legacy_payouts_migration,
            migrations.RunPython.noop,
        ),
    ]
