# core/urls_client.py
from django.urls import path
from .views import client_views as v

app_name = "client"

urlpatterns = [
    path("",                                       v.dashboard,              name="dashboard"),
    path("suspended/",                             v.suspended,              name="suspended"),

    # ── PDF Send Pipeline ─────────────────────────────────────────────────
    path("send/",                                  v.upload_pdf,             name="upload_pdf"),
    path("send/<uuid:job_id>/scanning/",           v.send_scanning,          name="send_scanning"),
    path("send/<uuid:job_id>/scanning/status/",    v.send_scan_status,       name="send_scan_status"),
    path("send/<uuid:job_id>/preview/",            v.send_preview,           name="send_preview"),
    path("send/<uuid:job_id>/confirm/",            v.send_confirm,           name="send_confirm"),
    path("send/<uuid:job_id>/processing/",         v.send_processing,        name="send_processing"),
    path("send/<uuid:job_id>/processing/status/",  v.send_processing_status, name="send_processing_status"),
    path("send/<uuid:job_id>/complete/",           v.send_complete,          name="send_complete"),

    # ── History ───────────────────────────────────────────────────────────
    path("history/",                               v.send_history,           name="send_history"),
    path("history/<uuid:job_id>/",                 v.job_detail,             name="job_detail"),

    # ── Staff Directory ───────────────────────────────────────────────────
    path("employees/",                             v.employee_list,          name="employee_list"),
    path("employees/import/",                      v.employee_import,        name="employee_import"),

    # ── Account ───────────────────────────────────────────────────────────
    path("account/",                               v.account,                name="account"),
]
