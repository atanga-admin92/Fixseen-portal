# config/settings/base.py
"""
Base settings shared across all environments.
Production overrides live in production.py, dev in development.py.
"""
from pathlib import Path
import environ
import os

# ─── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ─── Environment ─────────────────────────────────────────────────────────────
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG       = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ─── Auth ────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL     = "core.User"
LOGIN_URL           = "login"
LOGIN_REDIRECT_URL  = "/"
LOGOUT_REDIRECT_URL = "login"

# ─── Apps ────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    # Third-party
    "anymail",
    "django_celery_beat",
    "django_celery_results",
    # Project
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.MaintenanceFreezeMiddleware",   # Dec 26-31 block
    "core.middleware.SuspendedAccountMiddleware",    # Account suspension guard
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.global_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ─── Database ────────────────────────────────────────────────────────────────
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR}/db.sqlite3")
}

# ─── Password Validation ─────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ─── Internationalisation ────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE     = "Africa/Lusaka"
USE_I18N      = True
USE_TZ        = True

# ─── Static & Media ──────────────────────────────────────────────────────────
STATIC_URL  = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL  = "/media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── Sessions ────────────────────────────────────────────────────────────────
SESSION_ENGINE          = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE      = 3600 * 8   # 8 hours
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# ─── Celery ──────────────────────────────────────────────────────────────────
CELERY_BROKER_URL         = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND     = "django-db"
CELERY_CACHE_BACKEND      = "default"
CELERY_ACCEPT_CONTENT     = ["json"]
CELERY_TASK_SERIALIZER    = "json"
CELERY_RESULT_SERIALIZER  = "json"
CELERY_TIMEZONE           = TIME_ZONE
CELERY_BEAT_SCHEDULER     = "django_celery_beat.schedulers:DatabaseScheduler"

# Hard concurrency cap — prevents OOM on 4GB Hetzner VPS
# Set via start command: celery -A config worker --concurrency=2
CELERY_WORKER_CONCURRENCY = 2

# ─── Email ───────────────────────────────────────────────────────────────────
EMAIL_BACKEND    = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST       = env("EMAIL_HOST", default="")
EMAIL_PORT       = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS    = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER  = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL  = env("DEFAULT_FROM_EMAIL", default="FixSeen Portal <noreply@fixseen.com>")

# ─── Business Logic Constants ────────────────────────────────────────────────
PDF_HR_MASTER_KEY      = env("PDF_HR_MASTER_KEY", default="HR_Admin_Master_Key")
CREDIT_EXPIRY_DAYS     = 90
MAINTENANCE_FREEZE_START = (12, 26)   # Dec 26
MAINTENANCE_FREEZE_END   = (12, 31)   # Dec 31

# ─── Sentry (optional — add DSN to .env to activate) ─────────────────────────
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN and SENTRY_DSN.lower() not in ("none", "false", ""):
    import sentry_sdk
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.2)

# Postmark email via Anymail
ANYMAIL = {
    "POSTMARK_SERVER_TOKEN": env("POSTMARK_SERVER_TOKEN", default=""),
}
