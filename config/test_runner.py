"""Test runner tweaks.

Three things the suite needs that the default runner doesn't do:

* Password hashing dominates the runtime of a suite that creates users in almost
  every test (~2s/test with PBKDF2). Swapping in a fast hasher for tests only
  keeps the suite quick enough to actually run on every change.
* Placement tests upload audio samples, which would otherwise be written into
  the project's real MEDIA_ROOT and survive the run. Each run gets a throwaway
  directory instead.
* The test database is a real file rather than the in-memory default (see
  ``DATABASES["default"]["TEST"]`` for why the booking-lock tests need that),
  and every commit to a file is fsynced — ~200ms each on a spinning-rust or
  WSL2-backed filesystem, which turned the suite from 8s into minutes. Tests get
  ``synchronous=OFF``, below.
"""

import shutil
import tempfile

from django.conf import settings
from django.db.backends.signals import connection_created
from django.test.runner import DiscoverRunner


def disable_sqlite_fsync(sender, connection, **kwargs):
    """Stop fsyncing every commit, for test connections only.

    Safe here for the reason it would not be in production: the test database is
    rebuilt from migrations every run, so the only thing ``synchronous=FULL``
    buys is surviving a power cut mid-test — at the cost of an fsync per commit.

    Deliberately *not* touching ``journal_mode``. WAL would speed things up
    further, but it changes SQLite's locking rules (readers stop blocking
    writers), and the locking rules are exactly what the concurrency tests are
    there to exercise. ``synchronous`` affects durability only, not locking, so
    the booking lock still behaves here the way it will in production.
    """
    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA synchronous=OFF;")


class FastHasherDiscoverRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # Test-only. Production hashing is untouched by this.
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
        self._temp_media_root = tempfile.mkdtemp(prefix="quran-test-media-")
        settings.MEDIA_ROOT = self._temp_media_root
        # Every connection, including the ones the concurrency tests open in
        # their own threads.
        connection_created.connect(disable_sqlite_fsync)

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        connection_created.disconnect(disable_sqlite_fsync)
        shutil.rmtree(getattr(self, "_temp_media_root", ""), ignore_errors=True)

