"""SaaS Phase 3, step two: give the existing curriculum its owner.

All of the thinking lives in ``curriculum/legacy.py`` — deliberately, so the
decision this migration makes can be tested rather than only read. See that
module for the resolution rule and for why nothing here invents an academy.

On a fresh database (and on the test database) this is one ``EXISTS`` query and
nothing else. On an existing single-academy installation it assigns the tracks,
admits the users who already hold curriculum data, and converts legacy global
teacher specialties into academy-scoped ``TeacherTrack`` rows.

Reverse is a no-op: ``0004`` going backwards makes ownership optional again, and
un-assigning tracks would be inventing a "no owner" state that never existed as a
resting position. Nothing is deleted either way.
"""

from django.db import migrations

from curriculum.legacy import migrate_curriculum_to_organization


class Migration(migrations.Migration):

    dependencies = [
        ("curriculum", "0002_curriculum_organization_ownership"),
        ("accounts", "0003_organizationteacherconfiguration"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            migrate_curriculum_to_organization,
            migrations.RunPython.noop,
        ),
    ]
