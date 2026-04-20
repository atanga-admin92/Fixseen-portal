# core/secure_utils.py
"""
PDF processing engine for FixSeen Secure Payslip Portal.

Functions:
    scan_pdf_metadata()     — Dry-run scan: extract period + match employees
    split_and_encrypt_pdf() — Split bulk PDF + AES-256 encrypt each page
    build_payslip_password()— Password formula: NRC[:4] + FirstInit + LastInit

Encryption spec:
    - AES-256 via pikepdf (R=6 standard)
    - User password:  employee-specific (e.g. "1234CB")
    - Owner password: HR_Admin_Master_Key (set via settings.PDF_HR_MASTER_KEY)
"""
import re
import os
import logging
from pathlib import Path

import PyPDF2
import pikepdf
from django.conf import settings
from django.utils import timezone

from .models import Employee, SendJobLog

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns (compiled once at module load)
# ─────────────────────────────────────────────────────────────────────────────

PERIOD_RE = re.compile(
    r"PAY\s+STATEMENT\s+FOR[:\s]+([A-Z]+\s+\d{4})",
    re.IGNORECASE,
)
EMP_ID_RE = re.compile(
    r"\bID[:\s]+(\d{5,6})\b",
    re.IGNORECASE,
)
NRC_RE = re.compile(
    r"\bNRC[:\s#]*([\d/\-]+)",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Password formula
# ─────────────────────────────────────────────────────────────────────────────

def build_payslip_password(nrc: str, first_name: str, last_name: str) -> str:
    """
    Formula: <first 4 NRC digits> + <FirstName initial upper> + <LastName initial upper>
    Example: NRC="123456/78/9", first="CHISOMO", last="BANDA"  →  "1234CB"
    Falls back to "0000" if NRC is missing or too short.
    """
    nrc_digits = "".join(c for c in (nrc or "") if c.isdigit())
    nrc_base   = nrc_digits[:4].ljust(4, "0") if nrc_digits else "0000"
    fi         = first_name[0].upper() if first_name else "X"
    li         = last_name[0].upper()  if last_name  else "X"
    return f"{nrc_base}{fi}{li}"


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Async-safe PDF scan (the "Dry Run")
# ─────────────────────────────────────────────────────────────────────────────

def scan_pdf_metadata(file_path: str, company) -> dict:
    """
    Opens the bulk PDF and extracts:
        - payroll_period  : "APRIL 2026"
        - total_pages     : int
        - matched_count   : employees found in DB
        - unmatched_count : pages with no DB match (orphans)
        - matched_ids     : list of str employee_id
        - orphan_pages    : list of {page, extracted_id} dicts (for red highlight)

    Strict tenant isolation: only queries Employee.objects.filter(company=company).
    This runs inside a Celery worker — never call from a synchronous view directly.
    """
    payroll_period = "Unknown Period"
    matched_ids    = []
    orphan_pages   = []

    logger.info(f"[scan] Starting scan of {file_path} for company={company}")

    with open(file_path, "rb") as f:
        reader     = PyPDF2.PdfReader(f)
        total_pages = len(reader.pages)

        for page_num in range(total_pages):
            try:
                page = reader.pages[page_num]
                text = page.extract_text() or ""

                # Extract payroll period from first matching page only
                if payroll_period == "Unknown Period":
                    period_match = PERIOD_RE.search(text)
                    if period_match:
                        payroll_period = period_match.group(1).strip().upper()

                # Extract employee ID
                id_match = EMP_ID_RE.search(text)
                if id_match:
                    extracted_id = id_match.group(1).strip()
                    employee_exists = Employee.objects.filter(
                        company=company,
                        employee_id=extracted_id,
                        is_active=True,
                    ).exists()
                    if employee_exists:
                        matched_ids.append(extracted_id)
                    else:
                        orphan_pages.append({
                            "page":         page_num + 1,
                            "extracted_id": extracted_id,
                            "reason":       "ID not in staff directory",
                        })
                else:
                    orphan_pages.append({
                        "page":         page_num + 1,
                        "extracted_id": None,
                        "reason":       "No employee ID found on page",
                    })

            except Exception as e:
                logger.warning(f"[scan] Error reading page {page_num}: {e}")
                orphan_pages.append({
                    "page":         page_num + 1,
                    "extracted_id": None,
                    "reason":       f"Read error: {e}",
                })

    logger.info(
        f"[scan] Complete: period={payroll_period}, total={total_pages}, "
        f"matched={len(matched_ids)}, orphans={len(orphan_pages)}"
    )

    return {
        "period":         payroll_period,
        "total_pages":    total_pages,
        "matched_count":  len(matched_ids),
        "unmatched_count": len(orphan_pages),
        "matched_ids":    matched_ids,
        "orphan_pages":   orphan_pages,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Split + Encrypt  (runs in Celery worker)
# ─────────────────────────────────────────────────────────────────────────────

def split_and_encrypt_pdf(file_path: str, company, job) -> list:
    """
    Processes the bulk PDF page-by-page:
        1. Extracts employee ID from each page
        2. Looks up Employee in DB (strict tenant isolation)
        3. Writes a single-page PDF to a temp directory
        4. Encrypts it with AES-256 using the employee's payslip password
        5. Returns a list of SendJobLog-compatible dicts

    The encrypted file is what gets emailed. The unencrypted tmp file is
    deleted immediately after encryption.

    Owner password = settings.PDF_HR_MASTER_KEY  (allows HR to always open any file)
    User password  = employee.payslip_password    (e.g. "1234CB")
    """
    hr_master_key = settings.PDF_HR_MASTER_KEY
    output_dir    = Path(settings.MEDIA_ROOT) / "payslip_outputs" / str(job.id)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []

    with open(file_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)

        for page_num, page in enumerate(reader.pages, start=1):
            result = {
                "page_number":      page_num,
                "extracted_id":     None,
                "employee":         None,
                "recipient_email":  None,
                "recipient_name":   None,
                "payslip_password": None,
                "encrypted_path":   None,
                "status":           SendJobLog.DeliveryStatus.ORPHAN,
                "error_message":    "",
            }

            try:
                text = page.extract_text() or ""
                id_match = EMP_ID_RE.search(text)

                if not id_match:
                    result["error_message"] = "No employee ID found on page"
                    results.append(result)
                    continue

                extracted_id = id_match.group(1).strip()
                result["extracted_id"] = extracted_id

                try:
                    employee = Employee.objects.get(
                        company=company,
                        employee_id=extracted_id,
                        is_active=True,
                    )
                except Employee.DoesNotExist:
                    result["error_message"] = f"Employee ID {extracted_id} not in staff directory"
                    results.append(result)
                    continue

                # ── Write single-page unencrypted PDF ──────────────────────
                writer   = PyPDF2.PdfWriter()
                writer.add_page(page)
                tmp_path = output_dir / f"tmp_{page_num}_{extracted_id}.pdf"
                with open(tmp_path, "wb") as tmp_f:
                    writer.write(tmp_f)

                # ── AES-256 Encryption via pikepdf ─────────────────────────
                user_password = employee.payslip_password
                enc_path      = output_dir / f"{extracted_id}_{page_num}_payslip.pdf"

                with pikepdf.open(str(tmp_path)) as pdf:
                    pdf.save(
                        str(enc_path),
                        encryption=pikepdf.Encryption(
                            owner=hr_master_key,
                            user=user_password,
                            R=6,          # AES-256 (revision 6, PDF 2.0 standard)
                            allow=pikepdf.Permissions(
                                print_highres=True,
                                extract=False,    # Prevent copy-paste
                                modify_form=False,
                            ),
                        ),
                    )

                # ── Clean up unencrypted tmp file immediately ──────────────
                os.remove(tmp_path)

                result.update({
                    "employee":         employee,
                    "recipient_email":  employee.email,
                    "recipient_name":   employee.full_name,
                    "payslip_password": user_password,
                    "encrypted_path":   str(enc_path),
                    "status":           SendJobLog.DeliveryStatus.QUEUED,
                })

                logger.info(
                    f"[encrypt] Page {page_num}: employee={extracted_id}, "
                    f"password={user_password[:2]}****"
                )

            except Exception as e:
                logger.error(f"[encrypt] Error on page {page_num}: {e}", exc_info=True)
                result["error_message"] = str(e)
                # Clean up tmp if it exists
                tmp_path = output_dir / f"tmp_{page_num}_*.pdf"
                for f_tmp in output_dir.glob(f"tmp_{page_num}_*.pdf"):
                    try:
                        f_tmp.unlink()
                    except Exception:
                        pass

            results.append(result)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance freeze check
# ─────────────────────────────────────────────────────────────────────────────

def is_maintenance_freeze() -> bool:
    """
    Returns True during Dec 26–31 (inclusive).
    The send button should be blocked and a clear message shown.
    """
    from django.conf import settings
    now         = timezone.localtime()
    start_month, start_day = settings.MAINTENANCE_FREEZE_START
    end_month,   end_day   = settings.MAINTENANCE_FREEZE_END
    return (
        now.month == start_month
        and start_day <= now.day <= end_day
    )
