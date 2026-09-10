"""SaaS Phase 4, step three: make academy ownership required on Availability.

This migration is the reason ``0006`` could leave the column nullable: by the end
of a single ``migrate`` run, every stored availability window has an owner and the
database is what enforces it.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0007_backfill_availability_tenancy"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="availability",
            name="organization",
            field=models.ForeignKey(
                help_text=(
                    "The academy this availability window is published to. Set at "
                    "creation; not editable afterwards."
                ),
                on_delete=django.db.models.deletion.CASCADE,
                related_name="availabilities",
                to="organizations.organization",
            ),
        ),
    ]
