# core/tasks.py
"""
Celery async task workers for FixSeen Secure Payslip Portal.

Task flow for a send job:
    1. HR uploads PDF  →  view saves file, creates SendJob, calls task_scan_pdf.delay()
    2. task_scan_pdf() →  scans PDF, updates SendJob with preview data (status=PREVIEW)
    3. HR reviews preview, clicks "Confirm & Send"
    4. View calls task_process_send_job.delay()
    5. task_process_send_job() →  split + encrypt + email each payslip, updates logs

Worker start command (Coolify / docker-compose):
    celery -A config worker --concurrency=2 --loglevel=info

Beat scheduler (separate container):
    celery -A config beat --loglevel=info
"""
import logging
import os
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import Company, CreditBatch, SendJob, SendJobLog, ActivityLog
from .secure_utils import scan_pdf_metadata, split_and_encrypt_pdf, is_maintenance_freeze
from .services import deduct_credits

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Task 1: Scan PDF  (triggered immediately after upload)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def task_scan_pdf(self, job_id: str):
    """
    Performs the dry-run scan of the uploaded bulk PDF.
    Updates SendJob with scan results and sets status to PREVIEW.
    The HR user is then shown the preview screen.
    """
    from .models import SendJob
    try:
        job = SendJob.objects.select_related("company", "created_by").get(id=job_id)
    except SendJob.DoesNotExist:
        logger.error(f"[scan_task] SendJob {job_id} not found")
        return

    job.status  = SendJob.Status.SCANNING
    job.task_id = self.request.id
    job.save(update_fields=["status", "task_id"])

    try:
        result = scan_pdf_metadata(job.file_path, job.company)

        job.payroll_period  = result["period"]
        job.total_pages     = result["total_pages"]
        job.matched_count   = result["matched_count"]
        job.unmatched_count = result["unmatched_count"]
        job.status          = SendJob.Status.PREVIEW

        # Store orphan detail for the preview template
        # (kept in a transient session on the view, or persisted via JSON metadata)
        job.save(update_fields=[
            "payroll_period", "total_pages", "matched_count",
            "unmatched_count", "status",
        ])

        ActivityLog.objects.create(
            company    = job.company,
            actor      = job.created_by,
            event_type = ActivityLog.EventType.PDF_SCANNED,
            detail     = (
                f"Scanned {result['total_pages']} pages for {result['period']}. "
                f"Matched: {result['matched_count']}, Orphans: {result['unmatched_count']}"
            ),
            metadata   = result,
        )

        logger.info(f"[scan_task] Job {job_id} scan complete: {result}")

    except Exception as exc:
        logger.exception(f"[scan_task] Job {job_id} failed: {exc}")
        job.status        = SendJob.Status.FAILED
        job.error_message = str(exc)
        job.save(update_fields=["status", "error_message"])
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# Task 2: Process send job  (triggered after HR confirms on preview screen)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def task_process_send_job(self, job_id: str):
    """
    Full pipeline:
        1. Maintenance freeze check (Dec 26–31)
        2. Credit sufficiency check (FIFO)
        3. Split + AES-256 encrypt each matched page
        4. Email encrypted payslip to each employee
        5. Create SendJobLog per page
        6. Deduct credits
        7. Mark job as SENT or FAILED
    """
    from .models import SendJob
    try:
        job = SendJob.objects.select_related("company", "created_by").get(id=job_id)
    except SendJob.DoesNotExist:
        logger.error(f"[send_task] SendJob {job_id} not found")
        return

    # ── Maintenance freeze guard ───────────────────────────────────────────
    if is_maintenance_freeze():
        job.status        = SendJob.Status.CANCELLED
        job.error_message = "System is in maintenance freeze (Dec 26–31). No payslips can be sent."
        job.save(update_fields=["status", "error_message"])
        logger.warning(f"[send_task] Job {job_id} blocked by maintenance freeze")
        return

    # ── Credit check ───────────────────────────────────────────────────────
    from .models import User
    hr_user  = job.created_by
    credits  = hr_user.available_credits if hr_user else 0
    needed   = job.matched_count

    if credits < needed:
        job.status        = SendJob.Status.FAILED
        job.error_message = (
            f"Insufficient credits. Need {needed}, have {credits}. "
            f"Contact your administrator."
        )
        job.save(update_fields=["status", "error_message"])
        logger.warning(f"[send_task] Job {job_id} blocked: credits {credits} < needed {needed}")
        return

    # ── Begin processing ───────────────────────────────────────────────────
    job.status  = SendJob.Status.PROCESSING
    job.task_id = self.request.id
    job.save(update_fields=["status", "task_id"])

    try:
        pages = split_and_encrypt_pdf(job.file_path, job.company, job)

        logs          = []
        emails_sent   = 0
        emails_failed = 0

        for page in pages:
            log = SendJobLog(
                job             = job,
                employee        = page.get("employee"),
                page_number     = page["page_number"],
                extracted_id    = page.get("extracted_id") or "",
                recipient_email = page.get("recipient_email") or "",
                recipient_name  = page.get("recipient_name") or "",
                payslip_password= page.get("payslip_password") or "",
                status          = page["status"],
                error_message   = page.get("error_message") or "",
            )

            if page["status"] == SendJobLog.DeliveryStatus.QUEUED:
                # Only email matched (non-orphan) pages
                try:
                    _dispatch_payslip_email(
                        to_email     = page["recipient_email"],
                        to_name      = page["recipient_name"],
                        password_hint= page["payslip_password"],
                        company_name = job.company.name,
                        period       = job.payroll_period,
                        enc_path     = page["encrypted_path"],
                    )
                    log.status  = SendJobLog.DeliveryStatus.SENT
                    log.sent_at = timezone.now()
                    emails_sent += 1
                except Exception as email_err:
                    logger.error(f"[send_task] Email failed for {page['recipient_email']}: {email_err}")
                    log.status        = SendJobLog.DeliveryStatus.FAILED
                    log.error_message = str(email_err)
                    emails_failed    += 1

            logs.append(log)

        SendJobLog.objects.bulk_create(logs)

        # ── Deduct credits (FIFO, atomic) ──────────────────────────────────
        deduct_credits(job.company, emails_sent, job)

        # ── Finalise job ───────────────────────────────────────────────────
        job.emails_sent   = emails_sent
        job.emails_failed = emails_failed
        job.credits_used  = emails_sent
        job.status        = SendJob.Status.SENT
        job.completed_at  = timezone.now()
        job.save(update_fields=[
            "emails_sent", "emails_failed", "credits_used",
            "status", "completed_at",
        ])

        ActivityLog.objects.create(
            company    = job.company,
            actor      = job.created_by,
            event_type = ActivityLog.EventType.PAYSLIPS_SENT,
            detail     = (
                f"{emails_sent} payslips sent for {job.payroll_period}. "
                f"{emails_failed} failed."
            ),
            metadata   = {"job_id": str(job.id), "sent": emails_sent, "failed": emails_failed},
        )

        logger.info(f"[send_task] Job {job_id} complete: sent={emails_sent}, failed={emails_failed}")

    except Exception as exc:
        logger.exception(f"[send_task] Job {job_id} crashed: {exc}")
        job.status        = SendJob.Status.FAILED
        job.error_message = str(exc)
        job.completed_at  = timezone.now()
        job.save(update_fields=["status", "error_message", "completed_at"])

        ActivityLog.objects.create(
            company    = job.company,
            actor      = job.created_by,
            event_type = ActivityLog.EventType.SEND_FAILED,
            detail     = str(exc),
        )
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# Email dispatch helper
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_payslip_email(to_email, to_name, password_hint, company_name, period, enc_path):
    """
    Sends one encrypted payslip PDF to one employee.
    The body includes the password hint (first 2 chars shown, rest masked).
    """
    masked_pw = password_hint[:2] + "*" * (len(password_hint) - 2) if password_hint else "****"
    subject   = f"Your {period} Payslip — {company_name}"
    body = (
        f"Dear {to_name or 'Employee'},\n\n"
        f"Please find your payslip for {period} attached.\n\n"
        f"Your document is password protected.\n"
        f"Password hint: {masked_pw}\n"
        f"(Your password is your NRC first 4 digits + your initials.)\n\n"
        f"If you need assistance, please contact your HR department.\n\n"
        f"Regards,\n{company_name} HR\n"
    )
    email = EmailMessage(
        subject    = subject,
        body       = body,
        from_email = settings.DEFAULT_FROM_EMAIL,
        to         = [to_email],
    )
    if enc_path and os.path.exists(enc_path):
        email.attach_file(enc_path)
    email.send(fail_silently=False)


# ─────────────────────────────────────────────────────────────────────────────
# Periodic Task: Expire stale credit batches (Celery Beat — 1st of month)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task
def expire_stale_credits():
    """
    Voids all credit batches that have passed their expiry date.
    Runs via Celery Beat on the 1st of each month at 00:05.
    """
    now     = timezone.now()
    expired = CreditBatch.objects.filter(
        expires_at__lte=now,
        is_voided=False,
        remaining__gt=0,
    )
    count = expired.count()
    expired.update(is_voided=True, voided_at=now)

    # Log per company
    for company in Company.objects.filter(credit_batches__is_voided=True).distinct():
        ActivityLog.objects.create(
            company    = company,
            event_type = ActivityLog.EventType.CREDIT_EXPIRED,
            detail     = f"Monthly credit expiry run — {count} batch(es) voided system-wide",
        )

    logger.info(f"[beat] expire_stale_credits: voided {count} batch(es)")
    return {"voided": count}


# ─────────────────────────────────────────────────────────────────────────────
# Periodic Task: Notify clients whose credits expire within 14 days
# ─────────────────────────────────────────────────────────────────────────────

@shared_task
def notify_expiring_credits():
    """
    Sends an internal notification (email to HR user) if their credits
    expire within the next 14 days. Runs daily at 08:00.
    """
    from .models import User
    warning_date = timezone.now() + timedelta(days=14)
    expiring_batches = CreditBatch.objects.filter(
        expires_at__lte=warning_date,
        expires_at__gt=timezone.now(),
        is_voided=False,
        remaining__gt=0,
    ).select_related("company")

    notified = 0
    for batch in expiring_batches:
        hr_users = User.objects.filter(
            company=batch.company,
            role=User.Role.HR_ADMIN,
            is_active=True,
        )
        for user in hr_users:
            try:
                _send_expiry_warning(user, batch)
                notified += 1
            except Exception as e:
                logger.warning(f"[beat] Could not send expiry warning to {user.email}: {e}")

    logger.info(f"[beat] notify_expiring_credits: notified {notified} user(s)")
    return {"notified": notified}


def _send_expiry_warning(user, batch):
    days_left = (batch.expires_at - timezone.now()).days
    EmailMessage(
        subject    = f"⚠️ FixSeen: {batch.remaining} credits expire in {days_left} days",
        body       = (
            f"Hi,\n\n"
            f"Your account has {batch.remaining} credit(s) expiring on "
            f"{batch.expires_at.date()}.\n\n"
            f"Please contact your administrator to renew.\n\n"
            f"— FixSeen Portal"
        ),
        from_email = settings.DEFAULT_FROM_EMAIL,
        to         = [user.email],
    ).send(fail_silently=True)
