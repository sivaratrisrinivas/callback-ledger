"""Settings. Everything that differs between laptop, CI and Render comes from env vars."""
import os
import secrets
from datetime import timedelta
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def _secret(name):
    # No secrets live in the repo. If a value is missing we make a random one for this
    # process, which is fine for a single-process demo and for tests.
    value = os.environ.get(name)
    if not value:
        value = secrets.token_urlsafe(32)
        os.environ[name] = value  # so child threads and the in-process simulator agree
    return value


SECRET_KEY = _secret("DJANGO_SECRET_KEY")
DEBUG = os.environ.get("DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]

# Callback link signing key (server only) and the shared secret a partner signs its body with.
CALLBACK_LINK_SECRET = _secret("CALLBACK_LINK_SECRET")
PARTNER_SIGNING_SECRET = _secret("PARTNER_SIGNING_SECRET")
SIGNATURE_TOLERANCE_SECONDS = int(os.environ.get("SIGNATURE_TOLERANCE_SECONDS", "300"))
# How long a callback link keeps accepting (and parking) late replies after it was issued.
CALLBACK_LINK_TTL_SECONDS = int(os.environ.get("CALLBACK_LINK_TTL_SECONDS", str(24 * 3600)))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
# Where the simulated partner sends its HTTP calls (the app itself, over loopback).
SIMULATOR_TARGET = os.environ.get("SIMULATOR_TARGET", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "ledger",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

# Postgres is the target (DATABASE_URL=postgres://...). Without it we fall back to
# embedded SQLite, with IMMEDIATE transactions so writers queue instead of failing.
DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=0,
    )
}
if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    DATABASES["default"]["OPTIONS"] = {"transaction_mode": "IMMEDIATE", "timeout": 30}
    DATABASES["default"]["TEST"] = {"NAME": str(BASE_DIR / "test-db.sqlite3")}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# The React build (and demo.mp4) is served from the site root.
WHITENOISE_ROOT = BASE_DIR / "frontend" / "dist"
WHITENOISE_INDEX_FILE = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

# Celery: Redis (Render Key Value on the demo) when CELERY_BROKER_URL is set.
# Without a broker, timeouts are applied by the sweeper (`manage.py sweep`), never eagerly,
# because an eager ETA task would run the timeout path immediately.
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "")
CELERY_ENABLED = bool(CELERY_BROKER_URL)
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_ACKS_LATE = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {
    "sweep-expired-waits": {"task": "ledger.tasks.sweep_expired_waits", "schedule": timedelta(seconds=10)},
}

LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"ledger": {"handlers": ["console"], "level": "INFO"}},
}
