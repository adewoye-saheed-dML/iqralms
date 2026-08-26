# Quran Academy Platform

Django REST backend for a Quran/Arabic teaching academy. Product context lives in
`docs/mvp-spec.md`; the working conventions live in `CLAUDE.md`; each phase has a
spec under `specs/`.

## Running it from a fresh clone

There are two required pieces of setup and no way around either of them:
**PostgreSQL** (there is no SQLite fallback) and a **private storage backend**
(there is no public media route).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # nothing loads this automatically — see below
docker compose up -d --wait   # PostgreSQL on 5432, MinIO on 9000

set -a; . ./.env; set +a       # export it into your shell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

`.env` is deliberately not auto-loaded. The application reads the process
environment, so export it yourself or let your process manager do it — an env
file that is picked up implicitly is one you forget is there.

Already run PostgreSQL natively? Skip Docker entirely:

```bash
createdb quran
export DATABASE_URL=postgres:///quran   # local unix socket
```

API docs are at `/api/docs/` once the server is up.

## Environment variables

Every variable is documented with its default in
[`.env.example`](.env.example). The short version:

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | **yes** | PostgreSQL only. Missing or non-PostgreSQL fails at startup. |
| `DJANGO_DEBUG` | no | Defaults to **False**. Development opts in with `1`. |
| `DJANGO_PRODUCTION` | no | The explicit flag for the HTTPS-deployment settings and the hard requirements below. |
| `DJANGO_SECRET_KEY` | in production | Falls back to a committed development key otherwise; that key is rejected by name under `DJANGO_PRODUCTION=1`. |
| `DJANGO_ALLOWED_HOSTS` | in production | Comma separated. No wildcard default. |
| `DJANGO_STORAGE_BACKEND` | no | `s3` or `private-local`. Production requires `s3`. |
| `AWS_STORAGE_BUCKET_NAME` | with `s3` | Plus region/endpoint/credentials as the deployment needs. |
| `DJANGO_DB_SSLMODE` | no | Set to `require` or stricter on managed PostgreSQL. |

Never commit a real secret, a real bucket name tied to private infrastructure, or
production credentials.

## Tests

```bash
DATABASE_URL=postgres:///quran python manage.py test
```

The suite runs against PostgreSQL and requires no cloud bucket: uploads go to a
throwaway directory through the private local storage backend, and the presigned
URL path is tested against the real S3 backend with fake credentials (presigning
is local HMAC, not a network call).

The concurrency tests use `TransactionTestCase` and take the bulk of the runtime.
They are not optional — they are what proves two people cannot book one slot.

## Before deploying

```bash
python manage.py check --deploy --fail-level WARNING
```

With the documented production configuration this passes with no warnings. If it
reports anything, that is the configuration, not a check to be silenced.

Two things a deployment must arrange that the application cannot:

- **Static files.** `python manage.py collectstatic`, served by the proxy.
- **Uploaded media.** Nothing serves it. Placement recordings live in a private
  bucket and are reached only through short-lived signed URLs from
  `/api/curriculum/placements/{id}/audio-url/`. Do not add a media route.
