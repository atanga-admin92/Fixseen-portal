# core/services.py
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from .models import CreditBatch, ActivityLog
import logging

logger = logging.getLogger(__name__)


@transaction.atomic
def add_credits(company, amount: int, expires_at, added_by, notes: str = "") -> CreditBatch:
    batch = CreditBatch.objects.create(
        company    = company,
        amount     = amount,
        remaining  = amount,
        expires_at = expires_at,
        added_by   = added_by,
        notes      = notes,
    )
    ActivityLog.objects.create(
        company    = company,
        actor      = added_by,
        event_type = ActivityLog.EventType.CREDIT_ADDED,
        detail     = f"{amount} credits added, expires {expires_at.date()}",
        metadata   = {"batch_id": str(batch.id), "amount": amount},
    )
    return batch


@transaction.atomic
def deduct_credits(company, amount: int, job) -> bool:
    """FIFO deduction — earliest-expiry batch first. Atomic via select_for_update."""
    batches = (
        CreditBatch.objects
        .select_for_update()
        .filter(company=company, is_voided=False, expires_at__gt=timezone.now(), remaining__gt=0)
        .order_by("expires_at")
    )
    to_deduct = amount
    for batch in batches:
        if to_deduct <= 0:
            break
        take = min(batch.remaining, to_deduct)
        batch.remaining -= take
        batch.save(update_fields=["remaining"])
        to_deduct -= take
    return to_deduct == 0


@transaction.atomic
def suspend_client(user, by_user):
    user.suspend(by_user)
    user.company.credit_batches.filter(is_voided=False, expires_at__gt=timezone.now()).update(
        is_voided=True, voided_at=timezone.now()
    )
    ActivityLog.objects.create(
        company    = user.company,
        actor      = by_user,
        event_type = ActivityLog.EventType.USER_SUSPENDED,
        detail     = f"Suspended by {by_user.email}",
    )


@transaction.atomic
def unsuspend_client(user, by_user):
    user.unsuspend()
    ActivityLog.objects.create(
        company    = user.company,
        actor      = by_user,
        event_type = ActivityLog.EventType.USER_UNSUSPENDED,
        detail     = f"Unsuspended by {by_user.email}",
    )
