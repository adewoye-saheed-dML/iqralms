"""SaaS Phase 6: non-destructively remediate legacy assessment data.

Ensures all historical SessionAssessment and ProgressSnapshot participants
(students, teachers, reviewers, and generators) hold active memberships in the
owning academy, configures teacher roles, and fixes mismatched track foreign keys.
"""

from django.db import migrations

from assessment.legacy import remediate_legacy_assessment_migration


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0001_initial"),
        ("organizations", "0001_initial"),
        ("accounts", "0003_organizationteacherconfiguration"),
        ("curriculum", "0004_enforce_curriculum_tenancy"),
        ("scheduling", "0008_enforce_availability_tenancy"),
    ]

    operations = [
        migrations.RunPython(
            remediate_legacy_assessment_migration,
            migrations.RunPython.noop,
        ),
    ]
