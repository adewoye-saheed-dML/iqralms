"""
Django settings for the Quran Academy platform.

Phases 1-5 (accounts, curriculum, scheduling, routing, pricing + waitlist).
Secrets and environment-specific values read from the environment with
development-safe fallbacks so a fresh clone runs immediately.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# --- Core -------------------------------------------------------------------

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-insecure-key-change-before-any-deploy",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = [
    h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h
]


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
    # Local
    "accounts",
    "curriculum",
    "scheduling",
    "pricing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
# SQLite for Phase 1. See tech-debt.md — moves to Postgres before any real data.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            # Both of these exist for the booking lock (Phase 3.5). SQLite's
            # default is BEGIN DEFERRED, which takes no lock until the first
            # write. Two people booking one teacher's last slot would then both
            # BEGIN, both read the slot free, and both try to upgrade to a write
            # lock — and SQLite cannot let either wait, because each is waiting
            # on a lock the other holds. It breaks the tie by returning "database
            # is locked" immediately, ignoring the timeout below. The loser's
            # request fails as a 500 rather than a clean "already booked".
            #
            # BEGIN IMMEDIATE takes the write lock up front, so the second
            # booking queues at BEGIN instead of deadlocking mid-transaction,
            # and once it runs it sees the first booking committed and refuses
            # on the overlap rule like any other double-booking.
            "transaction_mode": "IMMEDIATE",
            # How long that second booking waits at BEGIN. Python's default is
            # already 5s; set explicitly because it stops being a detail once a
            # transaction can legitimately be made to wait.
            "timeout": 20,
        },
        "TEST": {
            # A real file, not the in-memory default. Django's in-memory test
            # database is opened with `cache=shared`, and shared-cache SQLite
            # locks per *table* and raises SQLITE_LOCKED ("database table is
            # locked") for a contended write — an error the busy timeout is
            # never consulted for, so the second booking dies instantly instead
            # of queueing. That is an artefact of shared-cache mode and nothing
            # to do with production, which is file-backed and blocks properly.
            # Testing the booking lock against it would prove nothing about the
            # thing being shipped, so the suite pays for a file instead.
            "NAME": BASE_DIR / "test_db.sqlite3",
        },
    }
}


# --- Authentication ---------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
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


# --- Static and media files -------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Placement audio samples land here. Local disk for now — see tech-debt.md.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uses a fast password hasher for tests only — see config/test_runner.py.
TEST_RUNNER = "config.test_runner.FastHasherDiscoverRunner"


# --- Video (Jitsi) ----------------------------------------------------------
# Each booking gets its own unguessable room name; the room comes into existence
# when the first participant joins it, so there is no API call and no key. The
# managed free tier is deliberate for now — self-hosting is a later
# optimisation, once volume justifies running a server (see docs/mvp-spec.md).

JITSI_DOMAIN = os.environ.get("JITSI_DOMAIN", "meet.jit.si")


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
        "Waitlist: preferred-teacher requests when that teacher is full."
    ),
    "VERSION": "0.5.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
