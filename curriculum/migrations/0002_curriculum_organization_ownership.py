"""SaaS Phase 3, step one: room for an owner, and the tenant-scoped teacher relation.

``Track.organization`` arrives nullable here on purpose. Existing rows have no
owner yet, and a column cannot be both required and unpopulated — so this
migration makes space, ``0003`` fills it, and ``0004`` enforces it. Tenant
ownership is *not* left permanently optional: the three run in a single
``migrate``, and the nullable window exists only between them.

``TeacherTrack`` is created here rather than after the backfill because the
backfill writes into it.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("curriculum", "0001_initial"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="track",
            name="organization",
            field=models.ForeignKey(
                help_text=(
                    "The academy that owns this track, and through it the track's "
                    "levels and placements. Set at creation; not editable "
                    "afterwards."
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tracks",
                to="organizations.organization",
            ),
        ),
        migrations.CreateModel(
            name="TeacherTrack",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "active",
                    models.BooleanField(
                        default=True,
                        help_text="Whether the teacher may currently be assigned this track here. Withdrawing eligibility keeps the row, the way a suspended membership does, so the record of what was once granted survives.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["membership_id", "track_id"],
            },
        ),
        migrations.AddField(
            model_name="teachertrack",
            name="membership",
            field=models.ForeignKey(
                help_text="The academy-and-teacher relationship this eligibility is for.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="teacher_tracks",
                to="organizations.organizationmembership",
            ),
        ),
        migrations.AddField(
            model_name="teachertrack",
            name="track",
            field=models.ForeignKey(
                help_text="A track owned by the same academy as the membership.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="teacher_tracks",
                to="curriculum.track",
            ),
        ),
        migrations.AddConstraint(
            model_name="teachertrack",
            constraint=models.UniqueConstraint(
                fields=("membership", "track"),
                name="unique_teacher_track_per_membership",
                violation_error_message="This teacher is already assigned that track in this academy. Change the existing assignment instead of adding a second one.",
            ),
        ),
    ]
