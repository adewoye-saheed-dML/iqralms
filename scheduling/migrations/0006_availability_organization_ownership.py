"""SaaS Phase 4, step one: room for an owner on Availability.

``Availability.organization`` arrives nullable here on purpose. Existing rows
have no owner yet, and a column cannot be both required and unpopulated — so this
migration makes space, ``0007`` fills it, and ``0008`` enforces it. Tenant
ownership is *not* left permanently optional: the three run in a single
``migrate``, and the nullable window exists only between them.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0001_initial"),
        ("scheduling", "0005_alter_teacherbookinglock_revision"),
    ]

    operations = [
        migrations.AddField(
            model_name="availability",
            name="organization",
            field=models.ForeignKey(
                help_text=(
                    "The academy this availability window is published to. Set at "
                    "creation; not editable afterwards."
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="availabilities",
                to="organizations.organization",
            ),
        ),
    ]
