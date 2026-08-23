"""Test runner tweaks.

Two things the suite needs that the default runner doesn't do:

* Password hashing dominates the runtime of a suite that creates users in almost
  every test (~2s/test with PBKDF2). Swapping in a fast hasher for tests only
  keeps the suite quick enough to actually run on every change.
* Placement tests upload audio samples, which would otherwise be written into
  the project's real MEDIA_ROOT and survive the run. Each run gets a throwaway
  directory instead.
"""

import shutil
import tempfile

from django.conf import settings
from django.test.runner import DiscoverRunner


class FastHasherDiscoverRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # Test-only. Production hashing is untouched by this.
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
        self._temp_media_root = tempfile.mkdtemp(prefix="quran-test-media-")
        settings.MEDIA_ROOT = self._temp_media_root

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        shutil.rmtree(getattr(self, "_temp_media_root", ""), ignore_errors=True)
