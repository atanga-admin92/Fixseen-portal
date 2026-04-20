# core/views/superadmin_views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Sum, Q
from django.utils import timezone
from datetime import timedelta

from ..models import Company, User, CreditBatch, SendJob, ActivityLog
from ..forms import AddCreditForm, CreateClientForm
from ..access import super_admin_required
from ..services import add_credits, suspend_client, unsuspend_client


@super_admin_required
def dashboard(request):
    now         = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    companies   = Company.objects.filter(is_active=True)

    stats = {
        "total_clients":  companies.count(),
        "active_credits": CreditBatch.objects.filter(
                              is_voided=False, expires_at__gt=now
                          ).aggregate(t=Sum("remaining"))["t"] or 0,
        "emails_sent":    SendJob.objects.filter(
                              status=SendJob.Status.SENT,
                              completed_at__gte=month_start,
                          ).aggregate(t=Sum("emails_sent"))["t"] or 0,
        "expiring_soon":  CreditBatch.objects.filter(
                              is_voided=False,
                              expires_at__range=(now, now + timedelta(days=14)),
                              remaining__gt=0,
                          ).values("company").distinct().count(),
    }
    recent_clients  = User.objects.filter(role=User.Role.HR_ADMIN).select_related("company").order_by("-date_joined")[:10]
    recent_activity = ActivityLog.objects.select_related("actor", "company").order_by("-created_at")[:20]

    return render(request, "superadmin/dashboard.html", {
        "stats": stats, "recent_clients": recent_clients, "recent_activity": recent_activity,
    })


@super_admin_required
def client_list(request):
    qs = User.objects.filter(role=User.Role.HR_ADMIN).select_related("company")
    q  = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(company__name__icontains=q) | Q(email__icontains=q))
    clients = [{"obj": u, "available": u.available_credits, "status": u.account_status, "next_expiry": u.next_expiry} for u in qs]
    return render(request, "superadmin/client_list.html", {"clients": clients, "q": q})


@super_admin_required
def client_detail(request, client_id):
    client = get_object_or_404(User, id=client_id, role=User.Role.HR_ADMIN)
    return render(request, "superadmin/client_detail.html", {
        "client":         client,
        "credit_form":    AddCreditForm(),
        "credit_history": client.company.credit_batches.order_by("-created_at") if client.company else [],
        "send_history":   SendJob.objects.filter(company=client.company).order_by("-created_at")[:20] if client.company else [],
        "activity_log":   ActivityLog.objects.filter(company=client.company).order_by("-created_at")[:30] if client.company else [],
        "available":      client.available_credits,
        "status":         client.account_status,
    })


@super_admin_required
def add_credits_view(request, client_id):
    client = get_object_or_404(User, id=client_id, role=User.Role.HR_ADMIN)
    if request.method == "POST":
        form = AddCreditForm(request.POST)
        if form.is_valid() and client.company:
            add_credits(
                company    = client.company,
                amount     = form.cleaned_data["amount"],
                expires_at = form.cleaned_data["expires_at"],
                added_by   = request.user,
                notes      = form.cleaned_data.get("notes", ""),
            )
            messages.success(request, f"{form.cleaned_data['amount']} credits added.")
        else:
            for err in form.errors.values():
                messages.error(request, err.as_text())
    return redirect("superadmin:client_detail", client_id=client_id)


@super_admin_required
def suspend_client_view(request, client_id):
    client = get_object_or_404(User, id=client_id, role=User.Role.HR_ADMIN)
    if request.method == "POST":
        if client.is_suspended:
            unsuspend_client(client, request.user)
            messages.success(request, f"{client.company} unsuspended.")
        else:
            suspend_client(client, request.user)
            messages.warning(request, f"{client.company} suspended and credits voided.")
    return redirect("superadmin:client_detail", client_id=client_id)


@super_admin_required
def create_client(request):
    from ..models import Company
    form = CreateClientForm(request.POST or None)
    companies = Company.objects.filter(is_active=True)
    if request.method == "POST" and form.is_valid():
        company_id = request.POST.get("company_id")
        company    = get_object_or_404(Company, id=company_id)
        form.save(company=company)
        messages.success(request, f"Client account created for {company.name}.")
        return redirect("superadmin:client_list")
    return render(request, "superadmin/create_client.html", {"form": form, "companies": companies})


@super_admin_required
def credits_overview(request):
    now     = timezone.now()
    batches = CreditBatch.objects.select_related("company", "added_by").order_by("-created_at")[:100]
    stats   = {
        "total_issued": CreditBatch.objects.aggregate(t=Sum("amount"))["t"] or 0,
        "active":       CreditBatch.objects.filter(is_voided=False, expires_at__gt=now).aggregate(t=Sum("remaining"))["t"] or 0,
        "expired":      CreditBatch.objects.filter(expires_at__lte=now, is_voided=False).aggregate(t=Sum("amount"))["t"] or 0,
    }
    return render(request, "superadmin/credits_overview.html", {"batches": batches, "stats": stats})


@super_admin_required
def activity_log(request):
    logs = ActivityLog.objects.select_related("actor", "company").order_by("-created_at")[:200]
    return render(request, "superadmin/activity_log.html", {"logs": logs})


@super_admin_required
def settings_view(request):
    return render(request, "superadmin/settings.html")
