# core/models.py
"""
Full data model for the FixSeen Secure Payslip Portal.

Tenant isolation is enforced at every level via the `company` FK.
Never query Employee, SendJob, or RecipientList without filtering by company.
"""
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone
from django.db.models import Sum
from datetime import timedelta
import uuid


# ─────────────────────────────────────────────────────────────────────────────
# User Manager
# ─────────────────────────────────────────────────────────────────────────────

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user  = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff",      True)
        extra_fields.setdefault("is_superuser",  True)
        extra_fields.setdefault("role",          User.Role.SUPER_ADMIN)
        return self.create_user(email, password, **extra_fields)


# ─────────────────────────────────────────────────────────────────────────────
# Company (Tenant)
# ─────────────────────────────────────────────────────────────────────────────

class Company(models.Model):
    """
    One Company = one HR client tenant.
    All employee data and send jobs are scoped to a company.
    """
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name         = models.CharField(max_length=255)
    slug         = models.SlugField(unique=True)
    email_domain = models.CharField(max_length=255, blank=True,
                                    help_text="Optional: used for V2 SSO matching")
    created_at   = models.DateTimeField(default=timezone.now)
    is_active    = models.BooleanField(default=True)

    # V2 BYOS (Bring Your Own Storage) fields
    byos_provider   = models.CharField(max_length=20, blank=True,
                                        choices=[("s3","AWS S3"),("azure","Azure Blob"),("","None")])
    byos_bucket     = models.CharField(max_length=255, blank=True)
    byos_key_id     = models.CharField(max_length=512, blank=True)
    byos_secret_key = models.CharField(max_length=512, blank=True)  # Encrypt at rest in V2

    class Meta:
        ordering      = ["name"]
        verbose_name  = "Company"
        verbose_name_plural = "Companies"

    def __str__(self):
        return self.name


# ─────────────────────────────────────────────────────────────────────────────
# User
# ─────────────────────────────────────────────────────────────────────────────

class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        HR_ADMIN    = "hr_admin",    "HR Admin"

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email        = models.EmailField(unique=True)
    company      = models.ForeignKey(Company, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="users")
    role         = models.CharField(max_length=20, choices=Role.choices, default=Role.HR_ADMIN)
    is_active    = models.BooleanField(default=True)
    is_staff     = models.BooleanField(default=False)
    date_joined  = models.DateTimeField(default=timezone.now)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_by = models.ForeignKey("self", null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="suspended_users")

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.company.name if self.company else self.email

    @property
    def is_super_admin(self):
        return self.role == self.Role.SUPER_ADMIN

    @property
    def is_hr_admin(self):
        return self.role == self.Role.HR_ADMIN

    @property
    def is_suspended(self):
        return self.suspended_at is not None

    @property
    def available_credits(self):
        if self.is_suspended or not self.company:
            return 0
        return (
            CreditBatch.objects
            .filter(company=self.company, expires_at__gt=timezone.now(), is_voided=False)
            .aggregate(total=Sum("remaining"))["total"] or 0
        )

    @property
    def next_expiry(self):
        batch = (
            CreditBatch.objects
            .filter(company=self.company, expires_at__gt=timezone.now(),
                    is_voided=False, remaining__gt=0)
            .order_by("expires_at").first()
        )
        return batch.expires_at if batch else None

    @property
    def account_status(self):
        if self.is_suspended:
            return "suspended"
        credits = self.available_credits
        if credits == 0:
            return "no_credits"
        expiry = self.next_expiry
        if expiry and (expiry - timezone.now()) < timedelta(days=14):
            return "expiring"
        return "active"

    def suspend(self, by_user):
        self.suspended_at = timezone.now()
        self.suspended_by = by_user
        self.save(update_fields=["suspended_at", "suspended_by"])

    def unsuspend(self):
        self.suspended_at = None
        self.suspended_by = None
        self.save(update_fields=["suspended_at", "suspended_by"])


# ─────────────────────────────────────────────────────────────────────────────
# Employee  ← Core model — the staff directory per company
# ─────────────────────────────────────────────────────────────────────────────

class Employee(models.Model):
    """
    Staff directory entry for one HR client.
    employee_id matches the "ID: XXXXX" value extracted from payslip PDFs.
    nrc is the National Registration Card number — used as the encryption key.
    """
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company     = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="employees")
    employee_id = models.CharField(max_length=20,
                                   help_text="Matches 'ID: XXXXX' on payslip PDF")
    first_name  = models.CharField(max_length=150)
    last_name   = models.CharField(max_length=150)
    email       = models.EmailField()
    nrc         = models.CharField(max_length=50, blank=True,
                                   help_text="National Registration Card number e.g. 123456/78/9")
    department  = models.CharField(max_length=150, blank=True)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(default=timezone.now)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("company", "employee_id")]
        ordering        = ["last_name", "first_name"]
        verbose_name    = "Employee"
        verbose_name_plural = "Employees"

    def __str__(self):
        return f"{self.last_name} {self.first_name} ({self.employee_id})"

    @property
    def full_name(self):
        return f"{self.last_name} {self.first_name}"

    @property
    def payslip_password(self):
        """
        Password formula:
            first 4 digits of NRC  +  First Name initial (upper)  +  Last Name initial (upper)
        Example: NRC=123456/78/9, Name=BANDA CHISOMO → password = "1234CB"
        Falls back to "0000" if NRC is missing.
        """
        nrc_digits  = "".join(c for c in self.nrc if c.isdigit())
        nrc_base    = nrc_digits[:4] if len(nrc_digits) >= 4 else "0000"
        first_init  = self.first_name[0].upper() if self.first_name else "X"
        last_init   = self.last_name[0].upper()  if self.last_name  else "X"
        return f"{nrc_base}{first_init}{last_init}"


# ─────────────────────────────────────────────────────────────────────────────
# Credit Batch
# ─────────────────────────────────────────────────────────────────────────────

class CreditBatch(models.Model):
    """
    Credits are attached to a Company (tenant), not a User.
    FIFO depletion: always consume the soonest-expiring batch first.
    One PDF page = one credit.
    """
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company    = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="credit_batches")
    amount     = models.PositiveIntegerField()
    remaining  = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    is_voided  = models.BooleanField(default=False)
    voided_at  = models.DateTimeField(null=True, blank=True)
    added_by   = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="credits_added")
    notes      = models.TextField(blank=True)

    class Meta:
        ordering = ["expires_at"]

    def __str__(self):
        return f"{self.company} — {self.remaining}/{self.amount} (exp {self.expires_at.date()})"

    def save(self, *args, **kwargs):
        from django.conf import settings
        if not self.pk:
            self.remaining = self.amount
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=settings.CREDIT_EXPIRY_DAYS)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    @property
    def is_active(self):
        return not self.is_voided and not self.is_expired and self.remaining > 0

    def void(self):
        self.is_voided = True
        self.voided_at = timezone.now()
        self.save(update_fields=["is_voided", "voided_at"])


# ─────────────────────────────────────────────────────────────────────────────
# Send Job  — one job = one bulk PDF upload
# ─────────────────────────────────────────────────────────────────────────────

class SendJob(models.Model):
    class Status(models.TextChoices):
        PENDING    = "pending",    "Pending"
        SCANNING   = "scanning",   "Scanning PDF"
        PREVIEW    = "preview",    "Awaiting Confirmation"
        PROCESSING = "processing", "Processing"
        SENT       = "sent",       "Sent"
        FAILED     = "failed",     "Failed"
        CANCELLED  = "cancelled",  "Cancelled"

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company         = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="send_jobs")
    created_by      = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="send_jobs")
    file_name       = models.CharField(max_length=512, blank=True)
    file_path       = models.CharField(max_length=1024, blank=True)
    payroll_period  = models.CharField(max_length=100, blank=True)

    # Scan results (populated after async scan)
    total_pages     = models.PositiveIntegerField(default=0)
    matched_count   = models.PositiveIntegerField(default=0)
    unmatched_count = models.PositiveIntegerField(default=0)

    # Send results
    credits_used    = models.PositiveIntegerField(default=0)
    emails_sent     = models.PositiveIntegerField(default=0)
    emails_failed   = models.PositiveIntegerField(default=0)

    status          = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    task_id         = models.CharField(max_length=255, blank=True)  # Celery task ID
    error_message   = models.TextField(blank=True)
    created_at      = models.DateTimeField(default=timezone.now)
    completed_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.company} — {self.payroll_period} ({self.status})"


# ─────────────────────────────────────────────────────────────────────────────
# Send Job Log — per-employee delivery record
# ─────────────────────────────────────────────────────────────────────────────

class SendJobLog(models.Model):
    class DeliveryStatus(models.TextChoices):
        QUEUED    = "queued",    "Queued"
        SENT      = "sent",      "Sent"
        FAILED    = "failed",    "Failed"
        ORPHAN    = "orphan",    "No Match in DB"

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job             = models.ForeignKey(SendJob, on_delete=models.CASCADE, related_name="logs")
    employee        = models.ForeignKey(Employee, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="delivery_logs")
    page_number     = models.PositiveIntegerField()
    extracted_id    = models.CharField(max_length=20, blank=True)
    recipient_email = models.EmailField(blank=True)
    recipient_name  = models.CharField(max_length=255, blank=True)
    payslip_password= models.CharField(max_length=50, blank=True)
    status          = models.CharField(max_length=20, choices=DeliveryStatus.choices,
                                       default=DeliveryStatus.QUEUED)
    sent_at         = models.DateTimeField(null=True, blank=True)
    error_message   = models.TextField(blank=True)

    class Meta:
        ordering = ["page_number"]


# ─────────────────────────────────────────────────────────────────────────────
# Activity Log — immutable audit trail
# ─────────────────────────────────────────────────────────────────────────────

class ActivityLog(models.Model):
    class EventType(models.TextChoices):
        CREDIT_ADDED     = "credit_added",     "Credit Added"
        CREDIT_EXPIRED   = "credit_expired",   "Credit Expired"
        CREDIT_VOIDED    = "credit_voided",    "Credit Voided"
        PDF_UPLOADED     = "pdf_uploaded",     "PDF Uploaded"
        PDF_SCANNED      = "pdf_scanned",      "PDF Scanned"
        PAYSLIPS_SENT    = "payslips_sent",    "Payslips Sent"
        SEND_FAILED      = "send_failed",      "Send Failed"
        USER_SUSPENDED   = "user_suspended",   "User Suspended"
        USER_UNSUSPENDED = "user_unsuspended", "User Unsuspended"
        USER_LOGIN       = "user_login",       "User Login"

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company    = models.ForeignKey(Company, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="activity_logs")
    actor      = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="actions")
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    detail     = models.TextField(blank=True)
    metadata   = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_type} by {self.actor} at {self.created_at}"
