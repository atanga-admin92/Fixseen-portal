# config/settings/production.py
from .base import *

DEBUG = False

SECURE_SSL_REDIRECT          = True
SECURE_HSTS_SECONDS          = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD          = True
SECURE_CONTENT_TYPE_NOSNIFF  = True
SECURE_BROWSER_XSS_FILTER    = True
SESSION_COOKIE_SECURE        = True
CSRF_COOKIE_SECURE           = True
X_FRAME_OPTIONS              = "DENY"

# Trust Coolify's reverse proxy headers
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Logging — write to stdout so Coolify/Docker captures it
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "[{levelname}] {asctime} {module} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "core":   {"handlers": ["console"], "level": "INFO",    "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO",    "propagate": False},
    },
}
