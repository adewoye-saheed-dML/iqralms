"""Management command to purge expired class session recordings (60-day retention policy).

Usage:
    python manage.py purge_expired_recordings
    python manage.py purge_expired_recordings --dry-run
"""

from django.core.management.base import BaseCommand
from django.utils import timezone as dj_timezone

from scheduling.models import ClassSessionRecording, RecordingStatus


class Command(BaseCommand):
    help = (
        "Purge class session recordings older than their retention expiry date "
        "(default 60 days / 2 months) to prevent database and disk bloat."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many recordings would be purged without deleting them.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        now = dj_timezone.now()

        expired_recordings = ClassSessionRecording.objects.filter(expires_at__lte=now)
        count = expired_recordings.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] Found {count} expired session recordings eligible for purging."
                )
            )
            for rec in expired_recordings[:10]:
                self.stdout.write(
                    f"  - Recording #{rec.id}: {rec.title} (expired {rec.expires_at:%Y-%m-%d})"
                )
            return

        # Perform deletion
        deleted_count, _ = expired_recordings.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully purged {deleted_count} expired class session recordings."
            )
        )
