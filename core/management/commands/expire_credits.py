# core/management/commands/expire_credits.py
"""
Management command: python manage.py expire_credits

Add to crontab as a fallback if Celery Beat is not running:
    5 0 1 * * /path/to/venv/bin/python /app/manage.py expire_credits
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import CreditBatch, ActivityLog


class Command(BaseCommand):
    help = "Expires all overdue credit batches"

    def handle(self, *args, **options):
        now     = timezone.now()
        expired = CreditBatch.objects.filter(
            expires_at__lte=now, is_voided=False, remaining__gt=0
        )
        count = expired.count()
        expired.update(is_voided=True, voided_at=now)
        self.stdout.write(self.style.SUCCESS(f"Expired {count} credit batch(es)."))
