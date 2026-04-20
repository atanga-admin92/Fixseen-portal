# core/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from .models import Company, User, Employee, CreditBatch, SendJob, SendJobLog, ActivityLog


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display  = ("name", "slug", "is_active", "created_at")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display    = ("email", "company", "role", "is_active", "date_joined")
    list_filter     = ("role", "is_active")
    search_fields   = ("email", "company__name")
    ordering        = ("-date_joined",)
    readonly_fields = ("date_joined", "suspended_at")
    fieldsets = (
        (None,       {"fields": ("email", "password")}),
        ("Profile",  {"fields": ("company", "role")}),
        ("Status",   {"fields": ("is_active", "is_staff", "is_superuser", "suspended_at", "suspended_by")}),
        ("Dates",    {"fields": ("date_joined",)}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "company", "role", "password1", "password2")}),
    )


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display  = ("employee_id", "last_name", "first_name", "company", "department", "is_active")
    list_filter   = ("company", "is_active", "department")
    search_fields = ("employee_id", "last_name", "first_name", "email", "nrc")
    readonly_fields = ("payslip_password_display",)

    @admin.display(description="Payslip Password")
    def payslip_password_display(self, obj):
        return format_html(
            '<code style="background:#f0f0f0;padding:2px 6px;border-radius:4px;">{}</code>',
            obj.payslip_password
        )


@admin.register(CreditBatch)
class CreditBatchAdmin(admin.ModelAdmin):
    list_display  = ("company", "amount", "remaining", "expires_at", "is_voided", "added_by")
    list_filter   = ("is_voided", "company")
    search_fields = ("company__name",)
    readonly_fields = ("created_at", "voided_at")


@admin.register(SendJob)
class SendJobAdmin(admin.ModelAdmin):
    list_display  = ("company", "payroll_period", "matched_count", "emails_sent", "status", "created_at")
    list_filter   = ("status", "company")
    search_fields = ("company__name", "payroll_period", "file_name")
    readonly_fields = ("created_at", "completed_at", "task_id")


@admin.register(SendJobLog)
class SendJobLogAdmin(admin.ModelAdmin):
    list_display  = ("job", "page_number", "extracted_id", "recipient_email", "status")
    list_filter   = ("status",)
    search_fields = ("extracted_id", "recipient_email")


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display  = ("event_type", "company", "actor", "detail", "created_at")
    list_filter   = ("event_type", "company")
    readonly_fields = ("created_at",)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
