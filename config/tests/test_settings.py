"""Phase 6, scope 4 — the production configuration boundary.

Acceptance criteria covered here: 4 (no application code relies on SQLite-only
transaction configuration), 11 (with production settings enabled, DEBUG is False,
the development secret fallback is rejected, explicit hosts are required, and
Django does not expose local media) and 12 (``check --deploy`` passes under the
documented production configuration).

**How these tests work.** ``config/settings.py`` decides everything at import
time, which is what makes a misconfigured deployment fail at startup rather than
on the first request that happens to touch the mistake. So the tests import it
again, from source, under a controlled environment, and assert on the module that
comes out — or on the ``ImproperlyConfigured`` that does not.

The probe module is loaded under a *cleared* environment so a variable set in the
developer's shell cannot quietly satisfy a requirement the test is trying to
prove is enforced. That has caught the class of bug where a test passes on one
machine because ``DJANGO_SECRET_KEY`` happens to be exported there.
"""

import importlib.util
import io
import os
from pathlib import Path
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import SimpleTestCase, TestCase, override_settings

from config.env import env_bool, env_int, env_list, env_str
from curriculum.validators import MAX_PLACEMENT_AUDIO_BYTES

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.py"

#: A syntactically valid PostgreSQL URL pointing at nothing real. Settings
#: parsing never connects, so no database has to exist for these tests — and
#: CLAUDE.md forbids committing real infrastructure identifiers.
FAKE_DATABASE_URL = "postgres://someuser:somepassword@db.invalid:5432/quran"

#: Long enough to satisfy the length rule, and obviously not a real secret.
FAKE_SECRET_KEY = "test-only-not-a-real-secret-" + "x" * 40

#: The minimum a deployment must supply. Every test that wants a *valid*
#: production configuration starts from this and changes one thing.
PRODUCTION_ENV = {
    "DJANGO_PRODUCTION": "1",
    "DJANGO_SECRET_KEY": FAKE_SECRET_KEY,
    "DJANGO_ALLOWED_HOSTS": "api.example.invalid,example.invalid",
    "DATABASE_URL": FAKE_DATABASE_URL,
    "AWS_STORAGE_BUCKET_NAME": "quran-academy-placement-audio-test",
    "AWS_S3_REGION_NAME": "eu-west-1",
}

#: A development configuration: the bare minimum a fresh clone needs.
DEVELOPMENT_ENV = {"DATABASE_URL": "postgres:///quran_dev"}


def load_settings(**environment):
    """Import ``config/settings.py`` fresh under exactly ``environment``.

    Loaded under a new module name so the real, already-imported settings module
    is untouched — these tests must not be able to change the configuration the
    rest of the suite is running under.
    """
    spec = importlib.util.spec_from_file_location(
        "config._settings_under_test", SETTINGS_PATH
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, environment, clear=True):
        spec.loader.exec_module(module)
    return module


def production(**changes):
    """A valid production environment with ``changes`` applied.

    ``changes`` may add a variable or override one; passing ``None`` removes it,
    which is how the "a deployment forgot to set this" cases are written.
    """
    environment = dict(PRODUCTION_ENV)
    environment.update(changes)
    return load_settings(**{k: v for k, v in environment.items() if v is not None})


def development(**changes):
    """A valid development environment with ``changes`` applied."""
    environment = dict(DEVELOPMENT_ENV)
    environment.update(changes)
    return load_settings(**{k: v for k, v in environment.items() if v is not None})


class EnvParsingTests(SimpleTestCase):
    """``config/env.py`` — typed, and loud about a value it cannot parse.

    The rule worth its own tests: an *unset* variable takes the default, but a
    variable that is *set and unparseable* raises. ``DJANGO_DEBUG=flase`` must not
    quietly mean False, because "not in the true list" is how a security setting
    silently becomes permissive.
    """

    def test_a_bool_accepts_the_documented_spellings(self):
        for raw, expected in (
            ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
            ("0", False), ("false", False), ("No", False), ("off", False),
        ):
            with self.subTest(raw=raw):
                with mock.patch.dict(os.environ, {"PROBE": raw}):
                    self.assertIs(env_bool("PROBE", default=None), expected)

    def test_an_unparseable_bool_raises_rather_than_defaulting(self):
        for raw in ("flase", "maybe", "2", "y"):
            with self.subTest(raw=raw):
                with mock.patch.dict(os.environ, {"PROBE": raw}):
                    with self.assertRaises(ImproperlyConfigured):
                        env_bool("PROBE", default=False)

    def test_an_unset_or_blank_variable_takes_the_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIs(env_bool("PROBE", default=True), True)
            self.assertEqual(env_int("PROBE", default=7), 7)
            self.assertEqual(env_str("PROBE", default="fallback"), "fallback")
            self.assertEqual(env_list("PROBE", default=["a"]), ["a"])
        with mock.patch.dict(os.environ, {"PROBE": "   "}):
            self.assertIs(env_bool("PROBE", default=True), True)
            self.assertEqual(env_str("PROBE", default="fallback"), "fallback")

    def test_an_unparseable_int_raises(self):
        with mock.patch.dict(os.environ, {"PROBE": "600s"}):
            with self.assertRaises(ImproperlyConfigured):
                env_int("PROBE", default=0)

    def test_a_list_drops_blanks_and_whitespace(self):
        with mock.patch.dict(os.environ, {"PROBE": " a , ,b,, c "}):
            self.assertEqual(env_list("PROBE", default=[]), ["a", "b", "c"])

    def test_an_all_blank_list_takes_the_default(self):
        with mock.patch.dict(os.environ, {"PROBE": " , , "}):
            self.assertEqual(env_list("PROBE", default=["kept"]), ["kept"])


class DeploymentModeTests(SimpleTestCase):
    """DEBUG defaults off, and cannot be on in production."""

    def test_debug_defaults_to_false(self):
        """Criterion 11's first clause, and the inversion Phase 6 performed.

        Phases 1-5 defaulted ``DEBUG`` to True, so a deployment got tracebacks
        with settings in them unless it remembered to turn them off. Now
        development opts in.
        """
        self.assertFalse(development().DEBUG)

    def test_development_can_opt_in(self):
        settings = development(DJANGO_DEBUG="1")
        self.assertTrue(settings.DEBUG)

    def test_production_defaults_to_false_and_is_opt_in(self):
        self.assertFalse(development().PRODUCTION)
        self.assertTrue(production().PRODUCTION)

    def test_debug_and_production_together_are_refused(self):
        """DEBUG=True is never a production configuration, so this is not a
        combination to resolve silently in either direction."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            production(DJANGO_DEBUG="1")
        self.assertIn("DEBUG", str(caught.exception))


class SecretKeyTests(SimpleTestCase):
    """Criterion 11 — production requires a real, non-development secret."""

    def test_the_development_fallback_is_rejected_in_production(self):
        """The committed key is public, so deploying with it is deploying with no
        secret at all. Rejected *by name*, not merely by length."""
        settings = development()
        with self.assertRaises(ImproperlyConfigured) as caught:
            production(DJANGO_SECRET_KEY=settings.DEV_SECRET_KEY)
        self.assertIn("development fallback", str(caught.exception))

    def test_a_missing_secret_is_rejected_in_production(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            production(DJANGO_SECRET_KEY=None)
        self.assertIn("DJANGO_SECRET_KEY", str(caught.exception))

    def test_a_too_short_secret_is_rejected_in_production(self):
        with self.assertRaises(ImproperlyConfigured):
            production(DJANGO_SECRET_KEY="short")

    def test_a_real_secret_is_accepted(self):
        self.assertEqual(production().SECRET_KEY, FAKE_SECRET_KEY)

    def test_development_falls_back_so_a_fresh_clone_runs(self):
        settings = development()
        self.assertEqual(settings.SECRET_KEY, settings.DEV_SECRET_KEY)


class AllowedHostsTests(SimpleTestCase):
    """Criterion 11 — production requires explicit hosts."""

    def test_production_without_hosts_is_refused(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            production(DJANGO_ALLOWED_HOSTS=None)
        self.assertIn("DJANGO_ALLOWED_HOSTS", str(caught.exception))

    def test_production_hosts_are_parsed_as_a_list(self):
        self.assertEqual(
            production().ALLOWED_HOSTS,
            ["api.example.invalid", "example.invalid"],
        )

    def test_there_is_no_wildcard_default(self):
        """A wildcard would defeat the check: ALLOWED_HOSTS is what stops
        Host-header poisoning, so it cannot be guessed on a deployment's behalf."""
        self.assertNotIn("*", production().ALLOWED_HOSTS)

    def test_development_defaults_to_localhost(self):
        self.assertEqual(
            development().ALLOWED_HOSTS,
            ["localhost", "127.0.0.1", "[::1]"],
        )


class CorsSettingsTests(SimpleTestCase):
    """CORS configuration."""

    def test_production_does_not_permit_all_origins(self):
        self.assertFalse(getattr(production(), "CORS_ALLOW_ALL_ORIGINS", False))
    
    def test_production_defaults_to_no_cors_origins_if_unset(self):
        self.assertEqual(production().CORS_ALLOWED_ORIGINS, [])

    def test_production_reads_cors_origins(self):
        settings = production(DJANGO_CORS_ALLOWED_ORIGINS="https://app.example.invalid,https://other.example.invalid")
        self.assertEqual(settings.CORS_ALLOWED_ORIGINS, ["https://app.example.invalid", "https://other.example.invalid"])

    def test_development_defaults_to_localhost_and_127(self):
        self.assertEqual(
            development().CORS_ALLOWED_ORIGINS,
            ["http://localhost:3000", "http://127.0.0.1:3000"],
        )


class DatabaseConfigurationTests(SimpleTestCase):
    """Criteria 4 and the database half of the configuration contract."""

    def test_a_missing_database_url_fails_with_an_actionable_message(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            load_settings()
        message = str(caught.exception)
        self.assertIn("DATABASE_URL", message)
        # The message has to say what to do, because this is the first error a
        # fresh clone hits.
        self.assertIn("docker compose", message)

    def test_there_is_no_sqlite_fallback(self):
        """The specific regression Phase 6 exists to prevent.

        SQLite became the canonical database because it was the convenient
        default. A URL naming it is now a configuration error, so the fallback
        cannot creep back in as "just for local".
        """
        for url in ("sqlite:///db.sqlite3", "sqlite://:memory:"):
            with self.subTest(url=url):
                with self.assertRaises(ImproperlyConfigured) as caught:
                    load_settings(DATABASE_URL=url)
                self.assertIn("not PostgreSQL", str(caught.exception))

    def test_a_malformed_url_does_not_quietly_become_something_else(self):
        with self.assertRaises(ImproperlyConfigured):
            load_settings(DATABASE_URL="not-a-url-at-all")

    def test_a_postgres_url_resolves_to_the_postgres_backend(self):
        database = production().DATABASES["default"]
        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(database["NAME"], "quran")
        self.assertEqual(database["HOST"], "db.invalid")
        self.assertEqual(database["PORT"], 5432)

    def test_no_sqlite_only_options_survive(self):
        """Criterion 4, asserted where the old options actually lived.

        ``transaction_mode: IMMEDIATE`` and the SQLite busy ``timeout`` were load
        bearing for the booking lock on SQLite — the lock deadlocked into
        "database is locked" without them. Both are meaningless to PostgreSQL, and
        leaving either behind would be a settings block that lies about what the
        application depends on.
        """
        options = production().DATABASES["default"].get("OPTIONS", {})
        self.assertNotIn("transaction_mode", options)
        self.assertNotIn("timeout", options)

    def test_the_test_database_needs_no_special_handling(self):
        """The file-backed test database went with SQLite too.

        It existed because shared-cache in-memory SQLite locks per table and
        raised SQLITE_LOCKED for a contended write, so the concurrency tests
        could not run against it. PostgreSQL needs none of that.
        """
        self.assertEqual(
            production().DATABASES["default"].get("TEST", {}), {}
        )

    def test_ssl_mode_is_applied_when_the_deployment_asks_for_it(self):
        settings = production(DJANGO_DB_SSLMODE="require")
        self.assertEqual(
            settings.DATABASES["default"]["OPTIONS"]["sslmode"], "require"
        )

    def test_ssl_mode_stays_opt_in(self):
        """A local socket or container offers no TLS, so defaulting it on would
        break development for a setting only a managed database needs."""
        options = development().DATABASES["default"].get("OPTIONS", {})
        self.assertNotIn("sslmode", options)

    def test_connection_lifetime_is_typed_and_configurable(self):
        self.assertEqual(
            production().DATABASES["default"]["CONN_MAX_AGE"], 600
        )
        pooled = production(DJANGO_DB_CONN_MAX_AGE="0")
        self.assertEqual(pooled.DATABASES["default"]["CONN_MAX_AGE"], 0)

    def test_a_non_numeric_connection_lifetime_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            production(DJANGO_DB_CONN_MAX_AGE="ten minutes")


class StorageConfigurationTests(SimpleTestCase):
    """Criterion 11's last clause — Django does not expose local media."""

    def test_there_is_no_media_url_setting_at_all(self):
        """The single line that made recitation samples public.

        Not "MEDIA_URL is empty" but "MEDIA_URL is not defined": there is nothing
        for a template, a serializer or a URLconf to build a public path from.
        """
        for label, loader in (("development", development), ("production", production)):
            with self.subTest(mode=label):
                self.assertFalse(hasattr(loader(), "MEDIA_URL"))

    def test_production_requires_the_bucket_backend(self):
        """Local disk plus SECRET_KEY signing is a development stand-in for a
        private bucket, not a substitute for one."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            production(DJANGO_STORAGE_BACKEND="private-local")
        self.assertIn("not a production configuration", str(caught.exception))

    def test_production_defaults_to_the_bucket_backend(self):
        settings = production()
        self.assertEqual(settings.STORAGE_BACKEND, "s3")
        self.assertEqual(
            settings.STORAGES["default"]["BACKEND"], "config.storage.PrivateS3Storage"
        )

    def test_production_requires_a_bucket_name(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            production(AWS_STORAGE_BUCKET_NAME=None)
        self.assertIn("AWS_STORAGE_BUCKET_NAME", str(caught.exception))

    def test_the_bucket_is_configured_private(self):
        """The three options that make it private, asserted individually.

        Any one of them being wrong reopens the hole the phase closed:
        ``querystring_auth`` off serves objects unsigned, a public-read
        ``default_acl`` makes them world-readable, and ``file_overwrite`` on lets
        a new sample land on a key an outstanding signed URL still points at.
        """
        options = production().STORAGES["default"]["OPTIONS"]
        self.assertTrue(options["querystring_auth"])
        self.assertIsNone(options["default_acl"])
        self.assertFalse(options["file_overwrite"])
        self.assertEqual(options["signature_version"], "s3v4")
        self.assertGreater(options["querystring_expire"], 0)

    def test_development_defaults_to_private_local_storage(self):
        settings = development()
        self.assertEqual(settings.STORAGE_BACKEND, "private-local")
        self.assertEqual(
            settings.STORAGES["default"]["BACKEND"],
            "config.storage.PrivateLocalStorage",
        )

    def test_an_s3_compatible_service_can_be_pointed_at_by_endpoint(self):
        """MinIO in docker-compose, or R2/B2 in production, is the same backend."""
        settings = production(
            AWS_S3_ENDPOINT_URL="https://minio.example.invalid:9000",
            AWS_S3_ADDRESSING_STYLE="path",
        )
        options = settings.STORAGES["default"]["OPTIONS"]
        self.assertEqual(options["endpoint_url"], "https://minio.example.invalid:9000")
        self.assertEqual(options["addressing_style"], "path")

    def test_an_unknown_backend_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            development(DJANGO_STORAGE_BACKEND="local")

    def test_the_upload_cap_bounds_the_request_body(self):
        """Django refuses an oversized body before any of our code runs, and the
        cap sits above the per-file limit so the student still gets the
        serializer's field error rather than a bare 400."""
        settings = development()
        self.assertGreater(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, MAX_PLACEMENT_AUDIO_BYTES)


class ProductionSecuritySettingsTests(SimpleTestCase):
    """The HTTPS-deployment settings, on under production and off otherwise."""

    def test_production_enables_the_full_set(self):
        settings = production()

        self.assertTrue(settings.SECURE_SSL_REDIRECT)
        self.assertEqual(
            settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https")
        )
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.CSRF_COOKIE_SECURE)
        self.assertTrue(settings.CSRF_COOKIE_HTTPONLY)
        self.assertEqual(settings.SECURE_HSTS_SECONDS, 31536000)
        self.assertTrue(settings.SECURE_HSTS_INCLUDE_SUBDOMAINS)
        self.assertTrue(settings.SECURE_HSTS_PRELOAD)
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.SECURE_REFERRER_POLICY, "same-origin")
        self.assertEqual(settings.X_FRAME_OPTIONS, "DENY")

    def test_development_does_not_redirect_to_https(self):
        """Gated on the explicit production flag rather than on ``not DEBUG``, so
        running the suite or a management command locally does not switch on TLS
        redirects against a server that has no TLS."""
        settings = development()
        self.assertFalse(getattr(settings, "SECURE_SSL_REDIRECT", False))
        self.assertFalse(getattr(settings, "SESSION_COOKIE_SECURE", False))
        self.assertEqual(getattr(settings, "SECURE_HSTS_SECONDS", 0), 0)

    def test_hsts_can_be_ratcheted_up_from_a_short_first_value(self):
        """A browser that has seen the header refuses plain HTTP for its whole
        duration, so a first deploy wants to start short."""
        settings = production(DJANGO_SECURE_HSTS_SECONDS="3600")
        self.assertEqual(settings.SECURE_HSTS_SECONDS, 3600)

    def test_csrf_trusted_origins_are_configurable(self):
        settings = production(
            DJANGO_CSRF_TRUSTED_ORIGINS="https://app.example.invalid,https://example.invalid",
        )
        self.assertEqual(
            settings.CSRF_TRUSTED_ORIGINS,
            ["https://app.example.invalid", "https://example.invalid"],
        )


class DeployCheckTests(SimpleTestCase):
    """Criterion 12 — ``manage.py check --deploy`` passes on production settings.

    Run in-process against the values the *real* production settings module
    produces, rather than a hand-written list of overrides that could drift away
    from it. ``--fail-level WARNING`` is what makes this meaningful: every deploy
    check is a warning, so at the default fail level the command would pass while
    reporting problems.

    ``DATABASES``, ``STORAGES`` and ``MEDIA_ROOT`` are held back from the
    override: they point at infrastructure that does not exist here, none of the
    deploy checks read them, and swapping the database out from under a running
    test suite is its own kind of mess.
    """

    #: Settings that name real infrastructure and are not what is under test.
    NOT_OVERRIDDEN = frozenset(
        {"DATABASES", "DATABASE_URL", "STORAGES", "MEDIA_ROOT", "TEST_RUNNER"}
    )

    def production_overrides(self):
        module = production()
        return {
            name: getattr(module, name)
            for name in dir(module)
            if name.isupper() and name not in self.NOT_OVERRIDDEN
        }

    def test_the_deploy_checks_pass_under_production_settings(self):
        output = io.StringIO()
        with override_settings(**self.production_overrides()):
            # Raises SystemCheckError if any check fires at WARNING or above.
            call_command(
                "check", "--deploy", "--fail-level", "WARNING", stdout=output, stderr=output
            )
        self.assertIn("no issues", output.getvalue())

    def test_the_deploy_checks_would_catch_a_weakened_setting(self):
        """Proof the check above is actually load bearing.

        A test that only ever runs the passing case cannot tell "the checks pass"
        from "the checks are not running". Turning one setting off must fail it.
        """
        overrides = self.production_overrides() | {"SECURE_HSTS_SECONDS": 0}
        with override_settings(**overrides):
            with self.assertRaises(SystemCheckError):
                call_command(
                    "check",
                    "--deploy",
                    "--fail-level",
                    "WARNING",
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )


class NoMediaRouteTests(TestCase):
    """Criterion 11, at the URLconf rather than in the settings.

    Phases 2-5 mounted ``static(settings.MEDIA_URL, ...)`` under DEBUG, so every
    recitation sample was fetchable by anyone who knew its path. Asserting the
    setting is gone is not quite enough — the route is what served the file, so
    the route is what this test goes after.
    """

    def test_the_development_media_route_is_gone_even_under_debug(self):
        with override_settings(DEBUG=True):
            response = self.client.get("/media/placements/1/recitation.mp3")
        self.assertEqual(response.status_code, 404)

    def test_the_root_urlconf_defines_no_media_pattern(self):
        from config import urls

        patterns = [str(getattr(p, "pattern", "")) for p in urls.urlpatterns]
        self.assertFalse([p for p in patterns if "media" in p])
