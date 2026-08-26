"""Test runner tweaks.

Two things the suite needs that the default runner does not do:

* Password hashing dominates the runtime of a suite that creates users in almost
  every test (~2s/test with PBKDF2). Swapping in a fast hasher for tests only
  keeps the suite quick enough to actually run on every change.
* Placement tests upload audio samples, which would otherwise be written into
  the project's real ``MEDIA_ROOT`` and survive the run. Each run gets a
  throwaway directory instead. ``PrivateLocalStorage`` resolves ``MEDIA_ROOT``
  lazily for exactly this reason, so redirecting the setting redirects every
  upload.

**What Phase 6 removed from this file.** Two SQLite workarounds went with SQLite
itself:

* a ``PRAGMA synchronous=OFF`` receiver on every new connection, because
  fsyncing each commit to a file-backed test database cost ~200ms and turned the
  suite from seconds into minutes;
* the file-backed test database that made that necessary, which existed because
  shared-cache in-memory SQLite locks per *table* and raises
  ``SQLITE_LOCKED`` for a contended write — so the booking-lock tests could not
  run against it.

PostgreSQL has row-level locks, so ``TransactionTestCase`` and the concurrency
tests work against an ordinary test database with no tuning at all.
"""

import shutil
import tempfile

from django.conf import settings
from django.test.runner import DiscoverRunner
from django.test.signals import setting_changed


class QuranAcademyTestRunner(DiscoverRunner):
    """Fast password hashing, and uploads pointed at a throwaway directory."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # Test-only. Production hashing is untouched by this.
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

        self._temp_media_root = tempfile.mkdtemp(prefix="quran-test-media-")
        settings.MEDIA_ROOT = self._temp_media_root
        # Assigning the setting directly does not tell an already-instantiated
        # storage about it. Sending the signal Django's own override_settings
        # sends is what clears the storage cache, so the first upload lands in
        # the temporary directory rather than the project's media/.
        setting_changed.send(
            sender=type(self),
            setting="MEDIA_ROOT",
            value=self._temp_media_root,
            enter=True,
        )

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        shutil.rmtree(getattr(self, "_temp_media_root", ""), ignore_errors=True)
