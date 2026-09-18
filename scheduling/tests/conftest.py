import pytest
from django.conf import settings
@pytest.fixture(autouse=True)
def patch_password_hashers(settings):
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

