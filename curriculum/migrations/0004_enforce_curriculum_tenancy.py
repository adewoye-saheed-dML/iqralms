"""SaaS Phase 3, step three: make academy ownership required, and slugs local.

Two changes, and the second is the one with product meaning. ``Track.slug`` loses
its global unique index: two academies may both teach ``tajweed``, and keeping the
old index would have made whichever academy signed up first the owner of that
word. Uniqueness moves to ``(organization, slug)``, which is what "unique within
this academy" actually means.

This migration is the reason ``0002`` could leave the column nullable: by the end
of a single ``migrate`` run, every stored track has an owner and the database is
what enforces it.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("curriculum", "0003_backfill_curriculum_tenancy"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="track",
            name="organization",
            field=models.ForeignKey(
                help_text=(
                    "The academy that owns this track, and through it the track's "
                    "levels and placements. Set at creation; not editable "
                    "afterwards."
                ),
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tracks",
                to="organizations.organization",
            ),
        ),
        migrations.AlterField(
            model_name="track",
            name="slug",
            field=models.SlugField(
                help_text="Unique within the owning academy, not across the platform.",
                max_length=80,
            ),
        ),
        migrations.AddConstraint(
            model_name="track",
            constraint=models.UniqueConstraint(
                fields=("organization", "slug"),
                name="unique_track_slug_per_organization",
                violation_error_message="This academy already has a track with that slug.",
            ),
        ),
    ]
