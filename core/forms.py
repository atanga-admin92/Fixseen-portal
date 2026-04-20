# core/forms.py
import csv
import io
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
from .models import User, CreditBatch


class LoginForm(forms.Form):
    email    = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "you@company.com", "autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Password"}))


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Current password"}))
    new_password     = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Min 8 characters"}), min_length=8)
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Confirm new password"}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_password") != cleaned.get("confirm_password"):
            raise ValidationError("Passwords do not match.")
        return cleaned


class CreateClientForm(forms.ModelForm):
    password  = forms.CharField(widget=forms.PasswordInput(), min_length=8)
    password2 = forms.CharField(widget=forms.PasswordInput(), label="Confirm password")

    class Meta:
        model  = User
        fields = ["email"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("password2"):
            raise ValidationError("Passwords do not match.")
        return cleaned

    def save(self, company, commit=True):
        user = super().save(commit=False)
        user.role    = User.Role.HR_ADMIN
        user.company = company
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class AddCreditForm(forms.ModelForm):
    DEFAULT_EXPIRY_DAYS = 90

    class Meta:
        model  = CreditBatch
        fields = ["amount", "expires_at", "notes"]
        widgets = {
            "expires_at": forms.DateInput(attrs={"type": "date"}),
            "notes":      forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. April invoice paid"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expires_at"].initial = (
            timezone.now() + timedelta(days=self.DEFAULT_EXPIRY_DAYS)
        ).strftime("%Y-%m-%d")
        self.fields["expires_at"].required = False
        self.fields["amount"].widget.attrs.update({"min": 1, "placeholder": "e.g. 500"})

    def clean_expires_at(self):
        expires = self.cleaned_data.get("expires_at")
        if not expires:
            return (timezone.now() + timedelta(days=self.DEFAULT_EXPIRY_DAYS)).date()
        if expires <= timezone.now().date():
            raise ValidationError("Expiry date must be in the future.")
        return expires

    def save(self, company, added_by, commit=True):
        batch = super().save(commit=False)
        batch.company   = company
        batch.added_by  = added_by
        batch.remaining = batch.amount
        if commit:
            batch.save()
        return batch


class PDFUploadForm(forms.Form):
    """Upload form for the bulk payslip PDF."""
    MAX_SIZE_MB = 200

    payslip_pdf = forms.FileField(
        label     = "Bulk Payslip PDF",
        help_text = f"Single PDF containing all employee payslips. Max {MAX_SIZE_MB} MB.",
        widget    = forms.FileInput(attrs={"accept": ".pdf"}),
    )

    def clean_payslip_pdf(self):
        f = self.cleaned_data["payslip_pdf"]
        if not f.name.lower().endswith(".pdf"):
            raise ValidationError("Only PDF files are accepted.")
        if f.content_type not in ["application/pdf", "application/x-pdf"]:
            raise ValidationError("File must be a valid PDF.")
        if f.size > self.MAX_SIZE_MB * 1024 * 1024:
            raise ValidationError(f"File must be under {self.MAX_SIZE_MB} MB.")
        return f


class EmployeeImportForm(forms.Form):
    """
    CSV import for staff directory.
    Expected columns: employee_id, first_name, last_name, email, nrc, department
    """
    csv_file = forms.FileField(
        label     = "Staff CSV File",
        help_text = "Columns: employee_id, first_name, last_name, email, nrc, department",
        widget    = forms.FileInput(attrs={"accept": ".csv"}),
    )

    def clean_csv_file(self):
        f = self.cleaned_data["csv_file"]
        if not f.name.lower().endswith(".csv"):
            raise ValidationError("Only .csv files are accepted.")
        if f.size > 5 * 1024 * 1024:
            raise ValidationError("File must be under 5 MB.")
        return f

    def parse_csv(self) -> list:
        f       = self.cleaned_data["csv_file"]
        content = f.read().decode("utf-8-sig", errors="ignore")
        reader  = csv.DictReader(io.StringIO(content))
        rows    = []
        for row in reader:
            employee_id = row.get("employee_id", "").strip()
            email       = row.get("email", "").strip()
            if employee_id and email:
                rows.append({
                    "employee_id": employee_id,
                    "first_name":  row.get("first_name", "").strip(),
                    "last_name":   row.get("last_name",  "").strip(),
                    "email":       email,
                    "nrc":         row.get("nrc",        "").strip(),
                    "department":  row.get("department", "").strip(),
                })
        return rows
