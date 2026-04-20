# config/celery.py
"""
Celery application entry point.
Workers are started via:
    celery -A config worker --concurrency=2 --loglevel=info
Beat scheduler:
    celery -A config beat --loglevel=info
"""
import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("fixseen")
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in all INSTALLED_APPS
app.autodiscover_tasks()

# ─── Periodic Tasks (Celery Beat) ────────────────────────────────────────────
app.conf.beat_schedule = {
    # Run on the 1st of every month at 00:05 to inject / expire credits
    "expire-stale-credits-monthly": {
        "task":     "core.tasks.expire_stale_credits",
        "schedule": crontab(hour=0, minute=5, day_of_month=1),
    },
    # Daily check — warn clients whose credits expire within 14 days
    "notify-expiring-credits-daily": {
        "task":     "core.tasks.notify_expiring_credits",
        "schedule": crontab(hour=8, minute=0),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
