# core/views/client_views.py
"""
Client (HR Admin) views — PDF upload, preview, async send, history.
All data access is scoped to request.user.company (strict tenant isolation).
"""
import os
import uuid
import logging
from pathlib import Path

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.utils import timezone
from django.http import JsonResponse

from ..models import Employee, SendJob, SendJobLog, ActivityLog, CreditBatch
from ..forms import PDFUploadForm, EmployeeImportForm, ChangePasswordForm
from ..secure_utils import is_maintenance_freeze
from ..tasks import task_scan_pdf, task_process_send_job
from ..access import client_required

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def dashboard(request):
    company     = request.user.company
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    recent_jobs = SendJob.objects.filter(company=company).order_by("-created_at")[:5]

    stats = {
        "available_credits": request.user.available_credits,
        "next_expiry":       request.user.next_expiry,
        "account_status":    request.user.account_status,
        "sent_this_month":   SendJob.objects.filter(
                                 company=company,
                                 status=SendJob.Status.SENT,
                                 completed_at__gte=month_start,
                             ).count(),
        "total_employees":   Employee.objects.filter(company=company, is_active=True).count(),
        "total_sent":        SendJob.objects.filter(company=company, status=SendJob.Status.SENT).count(),
        "maintenance_freeze": is_maintenance_freeze(),
    }

    return render(request, "client/dashboard.html", {
        "stats":       stats,
        "recent_jobs": recent_jobs,
    })


def suspended(request):
    return render(request, "client/suspended.html")


# ─────────────────────────────────────────────────────────────────────────────
# PDF Upload  →  triggers async scan
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def upload_pdf(request):
    """
    Step 1: HR uploads the bulk payslip PDF.
    File is saved to persistent MEDIA_ROOT (Docker volume).
    Async Celery task is triggered to scan the PDF.
    HR is immediately redirected to a "processing" waiting screen.
    """
    company = request.user.company

    if is_maintenance_freeze():
        messages.error(
            request,
            "⚠️ FixSeen is in maintenance mode (Dec 26–31). Sending is temporarily disabled."
        )
        return redirect("client:dashboard")

    if request.method == "POST":
        form = PDFUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = request.FILES["payslip_pdf"]

            # ── Save to persistent volume ──────────────────────────────────
            upload_dir = Path(settings.MEDIA_ROOT) / "payslip_uploads" / str(company.id)
            upload_dir.mkdir(parents=True, exist_ok=True)
            safe_name  = f"{uuid.uuid4()}_{uploaded.name.replace(' ', '_')}"
            file_path  = upload_dir / safe_name
            with open(file_path, "wb+") as dest:
                for chunk in uploaded.chunks():
                    dest.write(chunk)

            # ── Create pending SendJob ─────────────────────────────────────
            job = SendJob.objects.create(
                company    = company,
                created_by = request.user,
                file_name  = uploaded.name,
                file_path  = str(file_path),
                status     = SendJob.Status.PENDING,
            )

            ActivityLog.objects.create(
                company    = company,
                actor      = request.user,
                event_type = ActivityLog.EventType.PDF_UPLOADED,
                detail     = f"Uploaded {uploaded.name} ({uploaded.size} bytes)",
                metadata   = {"job_id": str(job.id), "file": uploaded.name},
            )

            # ── Fire async scan task ───────────────────────────────────────
            result = task_scan_pdf.delay(str(job.id))
            job.task_id = result.id
            job.save(update_fields=["task_id"])

            logger.info(f"[upload] Job {job.id} created, scan task {result.id} dispatched")
            return redirect("client:send_scanning", job_id=job.id)
    else:
        form = PDFUploadForm()

    # Credit check for template
    available = request.user.available_credits
    employees = Employee.objects.filter(company=company, is_active=True).count()

    return render(request, "client/send/upload.html", {
        "form":       form,
        "available":  available,
        "employees":  employees,
        "freeze":     is_maintenance_freeze(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Scanning  — polling screen shown while Celery task runs
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def send_scanning(request, job_id):
    """Shows a live-polling 'Scanning…' screen while the Celery scan task runs."""
    job = get_object_or_404(SendJob, id=job_id, company=request.user.company)
    if job.status == SendJob.Status.PREVIEW:
        return redirect("client:send_preview", job_id=job.id)
    if job.status == SendJob.Status.FAILED:
        messages.error(request, f"Scan failed: {job.error_message}")
        return redirect("client:upload_pdf")
    return render(request, "client/send/scanning.html", {"job": job})


@client_required
def send_scan_status(request, job_id):
    """JSON endpoint polled by the scanning page every 3 seconds."""
    job = get_object_or_404(SendJob, id=job_id, company=request.user.company)
    return JsonResponse({
        "status":   job.status,
        "redirect": job.status in [SendJob.Status.PREVIEW, SendJob.Status.FAILED],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Preview  — orphan handling, credit check, confirm button
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def send_preview(request, job_id):
    """
    Shows the dry-run preview:
      - Matched employees (green)
      - Orphan pages (red, highlighted — Send button disabled if any)
      - Credit sufficiency check
    Orphan = page in PDF with no matching active employee in the DB.
    """
    job = get_object_or_404(
        SendJob, id=job_id, company=request.user.company,
        status=SendJob.Status.PREVIEW,
    )
    available = request.user.available_credits
    needed    = job.matched_count
    has_orphans = job.unmatched_count > 0
    can_send  = (
        available >= needed
        and not has_orphans
        and not is_maintenance_freeze()
        and needed > 0
    )

    # Fetch orphan detail from activity log metadata
    scan_log = ActivityLog.objects.filter(
        company    = request.user.company,
        event_type = ActivityLog.EventType.PDF_SCANNED,
        metadata__job_id=str(job_id),
    ).order_by("-created_at").first()

    orphan_pages  = scan_log.metadata.get("orphan_pages", [])  if scan_log else []
    matched_ids   = scan_log.metadata.get("matched_ids",  [])  if scan_log else []

    return render(request, "client/send/preview.html", {
        "job":          job,
        "available":    available,
        "needed":       needed,
        "after":        available - needed,
        "has_orphans":  has_orphans,
        "orphan_pages": orphan_pages,
        "matched_ids":  matched_ids,
        "can_send":     can_send,
        "freeze":       is_maintenance_freeze(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Confirm Send  — triggers the full async processing task
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def send_confirm(request, job_id):
    """
    HR clicks 'Confirm & Send' on the preview screen.
    View returns immediately — Celery handles everything in the background.
    """
    if request.method != "POST":
        return redirect("client:send_preview", job_id=job_id)

    job = get_object_or_404(
        SendJob, id=job_id, company=request.user.company,
        status=SendJob.Status.PREVIEW,
    )

    if is_maintenance_freeze():
        messages.error(request, "⚠️ Sending is disabled during the Dec 26–31 maintenance freeze.")
        return redirect("client:send_preview", job_id=job_id)

    if request.user.available_credits < job.matched_count:
        messages.error(request, "Insufficient credits to complete this send.")
        return redirect("client:send_preview", job_id=job_id)

    # Fire the full processing task asynchronously
    result = task_process_send_job.delay(str(job.id))
    job.task_id = result.id
    job.status  = SendJob.Status.PROCESSING
    job.save(update_fields=["task_id", "status"])

    logger.info(f"[confirm] Job {job.id} dispatched to Celery task {result.id}")
    return redirect("client:send_processing", job_id=job.id)


# ─────────────────────────────────────────────────────────────────────────────
# Processing  — polling screen while emails are being sent
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def send_processing(request, job_id):
    job = get_object_or_404(SendJob, id=job_id, company=request.user.company)
    if job.status == SendJob.Status.SENT:
        return redirect("client:send_complete", job_id=job.id)
    if job.status == SendJob.Status.FAILED:
        messages.error(request, f"Send failed: {job.error_message}")
        return redirect("client:dashboard")
    return render(request, "client/send/processing.html", {"job": job})


@client_required
def send_processing_status(request, job_id):
    job = get_object_or_404(SendJob, id=job_id, company=request.user.company)
    return JsonResponse({
        "status":   job.status,
        "sent":     job.emails_sent,
        "failed":   job.emails_failed,
        "redirect": job.status in [SendJob.Status.SENT, SendJob.Status.FAILED],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Complete
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def send_complete(request, job_id):
    job  = get_object_or_404(SendJob, id=job_id, company=request.user.company)
    logs = job.logs.order_by("page_number")
    return render(request, "client/send/complete.html", {"job": job, "logs": logs})


# ─────────────────────────────────────────────────────────────────────────────
# Send History + Job Detail
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def send_history(request):
    jobs      = SendJob.objects.filter(company=request.user.company).order_by("-created_at")
    available = request.user.available_credits
    return render(request, "client/history.html", {"jobs": jobs, "available": available})


@client_required
def job_detail(request, job_id):
    job  = get_object_or_404(SendJob, id=job_id, company=request.user.company)
    logs = job.logs.order_by("page_number")
    return render(request, "client/job_detail.html", {"job": job, "logs": logs})


# ─────────────────────────────────────────────────────────────────────────────
# Staff Directory
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def employee_list(request):
    company   = request.user.company
    employees = Employee.objects.filter(company=company).order_by("last_name", "first_name")
    q = request.GET.get("q", "").strip()
    if q:
        employees = employees.filter(
            **{"last_name__icontains": q}
        ) | employees.filter(first_name__icontains=q) | employees.filter(employee_id__icontains=q)
    return render(request, "client/employees/list.html", {
        "employees": employees, "q": q,
    })


@client_required
def employee_import(request):
    """Bulk import employees from CSV: employee_id, first_name, last_name, email, nrc, department"""
    company = request.user.company
    form    = EmployeeImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        rows    = form.parse_csv()
        created = 0
        updated = 0
        for row in rows:
            _, was_created = Employee.objects.update_or_create(
                company     = company,
                employee_id = row["employee_id"],
                defaults    = {
                    "first_name": row.get("first_name", ""),
                    "last_name":  row.get("last_name",  ""),
                    "email":      row.get("email",      ""),
                    "nrc":        row.get("nrc",        ""),
                    "department": row.get("department", ""),
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
        messages.success(request, f"Import complete: {created} added, {updated} updated.")
        return redirect("client:employee_list")
    return render(request, "client/employees/import.html", {"form": form})


# ─────────────────────────────────────────────────────────────────────────────
# Account
# ─────────────────────────────────────────────────────────────────────────────

@client_required
def account(request):
    form = ChangePasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not request.user.check_password(form.cleaned_data["current_password"]):
            messages.error(request, "Current password is incorrect.")
        else:
            request.user.set_password(form.cleaned_data["new_password"])
            request.user.save()
            messages.success(request, "Password updated. Please sign in again.")
            return redirect("login")
    credit_batches = CreditBatch.objects.filter(
        company=request.user.company, is_voided=False
    ).order_by("expires_at")
    return render(request, "client/account.html", {
        "form":          form,
        "credit_batches": credit_batches,
    })
