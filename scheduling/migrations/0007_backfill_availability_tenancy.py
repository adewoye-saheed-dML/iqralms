"""SaaS Phase 4, step two: give the existing availability its owner.

All of the resolution logic lives in ``scheduling/legacy.py`` — deliberately,
so the decision this migration makes can be tested rather than only read. See that
module for the resolution rule and for why nothing here invents an academy.

On a fresh database (and on the test database) this is a no-op. On an existing
single-academy installation it assigns the windows and admits the teachers who
hold availability into that academy. On a multi-academy database it resolves
per-teacher or stops and asks the operator to name the owning organization via
``SAAS_LEGACY_SCHEDULING_ORGANIZATION``.
"""

from django.db import migrations

from scheduling.legacy import migrate_availability_to_organization


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0006_availability_organization_ownership"),
        ("accounts", "0003_organizationteacherconfiguration"),
        ("curriculum", "0004_enforce_curriculum_tenancy"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            migrate_availability_to_organization,
            migrations.RunPython.noop,
        ),
    ]
