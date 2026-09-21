"""
Django settings for the Quran Academy platform.

Phases 1-5 built the domain (accounts, curriculum, scheduling, routing, pricing +
waitlist). Phase 6 drew the line this module now enforces: development defaults
and production requirements are separate, and the production side *fails* rather
than falling back.

Three things changed shape in Phase 6 and are worth knowing before editing:

* ``DEBUG`` defaults to **False**. A fresh clone that wants the development
  experience opts in with ``DJANGO_DEBUG=1``; nothing gets it by accident.
* ``DATABASE_URL`` is **required**, and must name PostgreSQL. There is
  deliberately no SQLite fallback — a fallback is how SQLite became the
  canonical database in the first place. Missing or non-PostgreSQL raises at
  import with a message that says what to do (see docker-compose.yml).
* User uploads live in **private** storage in every environment. There is no
  ``MEDIA_URL`` and no media route in ``config/urls.py``; a recitation sample is
  reached through a short-lived signed URL and nothing else.

``DJANGO_PRODUCTION=1`` is the explicit flag that turns on the HTTPS-deployment
settings and the hard requirements (a real secret, explicit hosts, an S3-compatible
bucket). It is one flag rather than a settings-module hierarchy on purpose: the
spec asked to keep this module simple.

Every variable this file reads is documented in ``.env.example``.
"""

from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

# The one application import this module makes, and the reason it is safe:
# curriculum.validators holds constants and pure functions only, and touches
# neither models nor settings. The upload cap belongs next to the validator that
# enforces it and the tests that assert it, not duplicated here — but anything
# added to that module must keep it import-light, or loading settings breaks with
# AppRegistryNotReady.
from curriculum.validators import MAX_PLACEMENT_AUDIO_BYTES

from .env import env_bool, env_int, env_list, env_str

BASE_DIR = Path(__file__).resolve().parent.parent


# --- Deployment mode --------------------------------------------------------
# DEBUG defaults False (Phase 6): development opts in, production does not have
# to remember to opt out. PRODUCTION is separate and explicit — it gates the
# HTTPS settings and the hard requirements below, so a staging box behind TLS
# gets them without having to look like production in any other way.

DEBUG = env_bool("DJANGO_DEBUG", default=False)

PRODUCTION = env_bool("DJANGO_PRODUCTION", default=False)

if PRODUCTION and DEBUG:
    raise ImproperlyConfigured(
        "DJANGO_DEBUG and DJANGO_PRODUCTION are both on. DEBUG=True serves "
        "tracebacks with settings in them, so it is never a production "
        "configuration. Turn one of the two off."
    )


# --- Core -------------------------------------------------------------------

#: The fallback a fresh clone runs on. Named so it is recognisable in a diff,
#: and rejected by name under DJANGO_PRODUCTION — a deploy that forgot to set a
#: secret must fail loudly rather than ship a key that is in the repository.
DEV_SECRET_KEY = "dev-only-insecure-key-change-before-any-deploy"

#: Django's own ``check --deploy`` warns below 50 characters; matching it here
#: means a too-short secret is caught at startup rather than in a check run.
MIN_SECRET_KEY_LENGTH = 50

SECRET_KEY = env_str("DJANGO_SECRET_KEY", default="")

if PRODUCTION:
    if not SECRET_KEY:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is required when DJANGO_PRODUCTION=1. Generate "
            "one with: python -c 'from django.core.management.utils import "
            "get_random_secret_key as k; print(k())'"
        )
    if SECRET_KEY == DEV_SECRET_KEY:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is still the development fallback, which is "
            "committed to this repository and therefore public. Generate a real "
            "one before deploying."
        )
    if len(SECRET_KEY) < MIN_SECRET_KEY_LENGTH:
        raise ImproperlyConfigured(
            f"DJANGO_SECRET_KEY is {len(SECRET_KEY)} characters; at least "
            f"{MIN_SECRET_KEY_LENGTH} are needed for the signing it is used for "
            "(session cookies, password-reset tokens, and the placement-audio "
            "access tokens added in Phase 6)."
        )
elif not SECRET_KEY:
    SECRET_KEY = DEV_SECRET_KEY

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    # Development only. Production supplies its own or does not start.
    default=[] if PRODUCTION else ["localhost", "127.0.0.1", "[::1]"],
)

if PRODUCTION and not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS is required when DJANGO_PRODUCTION=1 (comma "
        "separated, e.g. 'api.example.com'). There is no wildcard default: "
        "ALLOWED_HOSTS is what stops Host-header poisoning, so guessing it "
        "would defeat the check."
    )

CORS_ALLOWED_ORIGINS = env_list(
    "DJANGO_CORS_ALLOWED_ORIGINS",
    default=[] if PRODUCTION else ["http://localhost:3000", "http://127.0.0.1:3000"],
)

FRONTEND_BASE_URL = env_str(
    "FRONTEND_BASE_URL",
    default="http://localhost:3000",
)


# --- Applications -----------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework.authtoken",
    "dj_rest_auth",
    "drf_spectacular",
    "corsheaders",
    "storages",
    # Local
    "accounts",
    "curriculum",
    "scheduling",
    "pricing",
    "assessment",
    "payouts",
    "organizations",
    "notifications",
    "imports",
    "audit_logs",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "audit_logs.middleware.RequestIDMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# --- Database ---------------------------------------------------------------
# PostgreSQL, required, no fallback (Phase 6). SQLite ran Phases 1-5 and left two
# marks that are both gone now: a ``transaction_mode: IMMEDIATE`` option, which
# existed because SQLite takes its write lock too late for the booking lock, and
# a file-backed test database, which existed because shared-cache in-memory
# SQLite locks per table. Neither applies to PostgreSQL, which has actual row
# locks — see scheduling.models.TeacherBookingLock, and the test in
# config/tests/test_settings.py that keeps either from creeping back.

DATABASE_URL = env_str("DATABASE_URL", default="")

if not DATABASE_URL:
    raise ImproperlyConfigured(
        "DATABASE_URL is required — this project has no SQLite fallback, "
        "because a fallback is how SQLite became the canonical database in the "
        "first place. Start a local PostgreSQL with `docker compose up -d db` "
        "and use the DATABASE_URL from .env.example, or point it at any "
        "PostgreSQL you already run."
    )

#: Only PostgreSQL. Named engines rather than a scheme check because
#: dj_database_url maps several schemes (postgres://, postgresql://, postgis://)
#: onto these, and the engine is what the application actually depends on.
SUPPORTED_DB_ENGINES = frozenset(
    {
        "django.db.backends.postgresql",
        "django.contrib.gis.db.backends.postgis",
    }
)


def _parse_database_url(url):
    """``DATABASE_URL`` as a Django DATABASES entry, or a clear failure.

    ``dj_database_url`` raises its own exceptions for a URL it cannot read, and
    those surface as a bare traceback naming a library the reader has no reason
    to know about. The spec asks for the database configuration to fail
    *clearly*, so anything it throws is re-raised as ``ImproperlyConfigured``
    with the variable's name in the message.
    """
    try:
        return dj_database_url.parse(
            url,
            # Persistent connections: the default of 0 opens a new backend per
            # request, which is the single cheapest thing to get wrong on managed
            # PostgreSQL. 0 is still available for a pooled deployment
            # (PgBouncer) that wants Django to hand connections straight back.
            conn_max_age=env_int("DJANGO_DB_CONN_MAX_AGE", default=600),
            conn_health_checks=True,
            # No fallback engine: an unrecognisable URL must fail rather than
            # quietly resolving to SQLite, which is the whole point of the
            # engine check below.
            engine=None,
        )
    except ImproperlyConfigured:
        raise
    except Exception as exc:
        raise ImproperlyConfigured(
            f"DATABASE_URL could not be parsed ({exc}). It must be a PostgreSQL "
            "URL, e.g. postgres://user:password@host:5432/dbname, or "
            "postgres:///dbname for a local unix socket."
        ) from exc


DATABASES = {"default": _parse_database_url(DATABASE_URL)}

if DATABASES["default"].get("ENGINE") not in SUPPORTED_DB_ENGINES:
    raise ImproperlyConfigured(
        f"DATABASE_URL resolved to engine "
        f"{DATABASES['default'].get('ENGINE')!r}, which is not PostgreSQL. "
        "PostgreSQL is the canonical database from Phase 6 onward (see "
        "CLAUDE.md); a URL naming anything else is a configuration mistake, not "
        "a supported mode."
    )

#: Managed PostgreSQL usually wants ``require`` or stricter; a local socket or
#: container does not offer TLS at all, so this stays opt-in rather than
#: defaulting to something that breaks development.
DATABASE_SSLMODE = env_str("DJANGO_DB_SSLMODE", default="")
if DATABASE_SSLMODE:
    DATABASES["default"].setdefault("OPTIONS", {})["sslmode"] = DATABASE_SSLMODE


# --- Authentication ---------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- Internationalization ---------------------------------------------------
# All datetimes are stored in UTC. Per-user display conversion happens at the
# serializer layer using User.timezone — never store local time.

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# --- Static files -----------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"


# --- Uploaded media: private in every environment ---------------------------
# There is deliberately no MEDIA_URL and no media route in config/urls.py. A
# recitation sample is a minor's voice, and Phases 2-5 served it off MEDIA_URL
# where anyone holding the path could fetch it. Access now goes through a
# short-lived signed URL and nothing else — see config/storage.py for the two
# backends and curriculum/audio.py for the one function that mints the URLs.
#
# Two backends, one interface:
#   s3            — an S3-compatible private bucket. Required in production.
#                   Signed URLs are the bucket's own presigned URLs.
#   private-local — MEDIA_ROOT on local disk, with url() *raising* rather than
#                   returning anything fetchable. Signed URLs point at a
#                   token-gated Django view. Development and tests.

#: Where private-local keeps its files. Not served by anything.
MEDIA_ROOT = env_str("DJANGO_MEDIA_ROOT", default=str(BASE_DIR / "media"))

STORAGE_BACKEND = env_str(
    "DJANGO_STORAGE_BACKEND",
    default="s3" if PRODUCTION else "private-local",
)

if PRODUCTION and STORAGE_BACKEND != "s3":
    raise ImproperlyConfigured(
        f"DJANGO_STORAGE_BACKEND={STORAGE_BACKEND!r} is not a production "
        "configuration. private-local keeps recitation samples on the "
        "application server's own disk and signs access with SECRET_KEY, which "
        "is a development stand-in for a private bucket, not a substitute for "
        "one. Set DJANGO_STORAGE_BACKEND=s3."
    )

#: How long a minted placement-audio URL stays usable. Long enough for the lead
#: to press play on a slow connection, short enough that a URL pasted into a
#: chat log is worthless by the time anyone reads it.
PLACEMENT_AUDIO_URL_TTL_SECONDS = env_int("DJANGO_PLACEMENT_AUDIO_URL_TTL", default=300)

if STORAGE_BACKEND == "s3":
    AWS_STORAGE_BUCKET_NAME = env_str("AWS_STORAGE_BUCKET_NAME", default="")
    if not AWS_STORAGE_BUCKET_NAME:
        raise ImproperlyConfigured(
            "AWS_STORAGE_BUCKET_NAME is required for DJANGO_STORAGE_BACKEND=s3."
        )
    AWS_S3_REGION_NAME = env_str("AWS_S3_REGION_NAME", default="") or None
    #: Set for MinIO, Cloudflare R2, Backblaze B2 or any other S3-compatible
    #: service; left unset for AWS itself.
    AWS_S3_ENDPOINT_URL = env_str("AWS_S3_ENDPOINT_URL", default="") or None
    #: Credentials are optional on purpose: an instance role or IRSA is the
    #: better way to supply them, and boto3 finds those itself.
    AWS_ACCESS_KEY_ID = env_str("AWS_ACCESS_KEY_ID", default="") or None
    AWS_SECRET_ACCESS_KEY = env_str("AWS_SECRET_ACCESS_KEY", default="") or None
    #: MinIO and R2 need path-style addressing; AWS accepts it too.
    AWS_S3_ADDRESSING_STYLE = env_str("AWS_S3_ADDRESSING_STYLE", default="virtual")

    STORAGES = {
        "default": {
            "BACKEND": "config.storage.PrivateS3Storage",
            "OPTIONS": {
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "region_name": AWS_S3_REGION_NAME,
                "endpoint_url": AWS_S3_ENDPOINT_URL,
                "access_key": AWS_ACCESS_KEY_ID,
                "secret_key": AWS_SECRET_ACCESS_KEY,
                "addressing_style": AWS_S3_ADDRESSING_STYLE,
                # The three that make the bucket private:
                #   querystring_auth — every url() is presigned, so an object is
                #     unreachable without a signature.
                #   default_acl None — send no ACL at all, which is what a
                #     bucket with ownership enforced requires; an explicit
                #     "private" would be rejected by such a bucket, and any
                #     public-read value would defeat the whole change.
                #   file_overwrite False — a re-submitted sample gets a new key
                #     rather than silently replacing one a signed URL may still
                #     be pointing at.
                "querystring_auth": True,
                "querystring_expire": PLACEMENT_AUDIO_URL_TTL_SECONDS,
                "default_acl": None,
                "file_overwrite": False,
                "signature_version": "s3v4",
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
elif STORAGE_BACKEND == "private-local":
    STORAGES = {
        "default": {
            "BACKEND": "config.storage.PrivateLocalStorage",
            # No "location": the storage resolves MEDIA_ROOT lazily, which is
            # what lets the test runner point it at a throwaway directory (and
            # override_settings(MEDIA_ROOT=...) work in a single test).
            "OPTIONS": {},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    raise ImproperlyConfigured(
        f"DJANGO_STORAGE_BACKEND={STORAGE_BACKEND!r} is not recognised. Use "
        "'s3' (an S3-compatible private bucket) or 'private-local' (local disk, "
        "development and tests only)."
    )


# --- Upload limits ----------------------------------------------------------
# The serializer is where a placement upload is rejected with a useful field
# error (curriculum/validators.py holds the size cap and the format allowlist).
# This is the blunter guard in front of it: Django refuses a request body over
# this before any of our code runs, so an oversized upload cannot be spooled in
# full just to be rejected afterwards. Kept above the per-file cap so that the
# error a student sees is the serializer's field error, not a bare 400 from
# Django's own request parsing.

#: Headroom for multipart overhead and the other fields in the request.
UPLOAD_HEADROOM_BYTES = 1 * 1024 * 1024

DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_PLACEMENT_AUDIO_BYTES + UPLOAD_HEADROOM_BYTES

#: Above this, an upload is spooled to a temporary file instead of being held in
#: memory. Lower than the cap on purpose: a legitimate recitation sample is
#: bigger than this, so the common case streams to disk rather than putting a
#: multi-megabyte buffer per concurrent upload in the worker's memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uses a fast password hasher for tests only — see config/test_runner.py.
TEST_RUNNER = "config.test_runner.QuranAcademyTestRunner"


# --- Video (Jitsi) ----------------------------------------------------------
# Each booking gets its own unguessable room name; the room comes into existence
# when the first participant joins it, so there is no API call and no key. The
# managed free tier is deliberate for now — self-hosting is a later
# optimisation, once volume justifies running a server (see docs/mvp-spec.md).

JITSI_DOMAIN = env_str("JITSI_DOMAIN", default="meet.jit.si")


# --- DRF / dj-rest-auth / spectacular ---------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

REST_AUTH = {
    "USE_JWT": False,
    "TOKEN_MODEL": "rest_framework.authtoken.models.Token",
    "SESSION_LOGIN": False,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Quran Academy API",
    "DESCRIPTION": (
        "Identity layer: users, roles, parent links, teacher profiles. "
        "Curriculum: tracks, levels, placement review. "
        "Scheduling: teacher availability, direct booking, Jitsi video. "
        "Routing: cohorts, capacity-based auto-assignment. "
        "Pricing: negotiated rates per student and level, lead-approved. "
        "Waitlist: preferred-teacher requests when that teacher is full. "
        "Assessment: per-session rubric scoring, lead review, family progress. "
        "Payouts: teacher payout records and period statements, lead-generated. "
        "Organizations: the multi-tenant foundation — academies and the "
        "memberships that grant access to one."
    ),
    "VERSION": "0.6.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Two serializers expose a field called "role" over *different* choice sets:
    # the full Role for reading a user, and the self-registerable subset for
    # signing up ('lead' is excluded, being the single academy-owner account).
    # Without these names drf-spectacular resolves the collision itself and warns
    # about it — and `check --deploy --fail-level WARNING` is part of this
    # project's definition of done, so an unnamed enum is a failing check rather
    # than a cosmetic nit.
    # Phase 8 adds two more "status" choice sets on top of the booking one, so
    # all three need names of their own for the same reason.
    "ENUM_NAME_OVERRIDES": {
        "RoleEnum": "accounts.models.Role.choices",
        "SelfRegisterableRoleEnum": "accounts.serializers.SELF_REGISTERABLE_ROLES",
        "BookingStatusEnum": "scheduling.models.BookingStatus.choices",
        "PayoutStatusEnum": "payouts.models.PayoutStatus.choices",
        "StatementStatusEnum": "payouts.models.StatementStatus.choices",
        # SaaS Phase 1 adds a second "role" concept and a third "status" one, both
        # deliberately independent of the account roles and booking statuses above
        # — so both need names, for the same failing-check reason.
        "OrganizationRoleEnum": "organizations.models.OrganizationRole.choices",
        "AssignableOrganizationRoleEnum": (
            "organizations.serializers.ASSIGNABLE_ORGANIZATION_ROLES"
        ),
        "InvitableOrganizationRoleEnum": (
            "organizations.serializers.INVITABLE_ORGANIZATION_ROLES"
        ),
        "MembershipStatusEnum": "organizations.models.MembershipStatus.choices",
    },
}


# --- Production security ----------------------------------------------------
# Gated on the explicit DJANGO_PRODUCTION flag rather than on `not DEBUG`, so
# that running the test suite or a management command locally does not switch on
# HTTPS redirects. Everything here assumes TLS terminates at a proxy in front of
# the application, which is what SECURE_PROXY_SSL_HEADER is for.

if PRODUCTION:
    #: Trust the proxy's protocol header. Only correct because the proxy is
    #: assumed to *set* it rather than pass a client value through — if that is
    #: not true of a given deployment, this line is the one to remove.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    #: The CSRF cookie is read by JavaScript in a browser client, so this is
    #: opt-out; a token-only API client never needs it and should leave it on.
    CSRF_COOKIE_HTTPONLY = env_bool("DJANGO_CSRF_COOKIE_HTTPONLY", default=True)
    #: Scheme-qualified origins, e.g. 'https://app.example.com'. Needed for any
    #: browser client on a different host from the API.
    CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

    #: One year, with subdomains and preload. Long values are hard to walk back
    #: — a browser that has seen this header refuses plain HTTP for the whole
    #: duration — so it is env-tunable for a first deploy that wants to start
    #: short and ratchet up.
    SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(
        "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
    )
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", default=True)

    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    #: Clickjacking: this API has no pages meant to be framed, admin included.
    X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = env_str(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = env_str("EMAIL_HOST", default="localhost")
EMAIL_PORT = env_int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", default=False)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env_str(
    "DEFAULT_FROM_EMAIL",
    default="notifications@quranacademy.local",
)

