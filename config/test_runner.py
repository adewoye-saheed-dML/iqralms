"""Test runner tweaks.

Password hashing dominates the runtime of a suite that creates users in almost
every test (~2s/test with PBKDF2). Swapping in a fast hasher for tests only
keeps the suite quick enough to actually run on every change.
"""

from django.conf import settings
from django.test.runner import DiscoverRunner


class FastHasherDiscoverRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # Test-only. Production hashing is untouched by this.
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
