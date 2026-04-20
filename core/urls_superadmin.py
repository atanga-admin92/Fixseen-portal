# core/urls_superadmin.py
from django.urls import path
from .views import superadmin_views as v

app_name = "superadmin"

urlpatterns = [
    path("",                                      v.dashboard,          name="dashboard"),
    path("clients/",                              v.client_list,        name="client_list"),
    path("clients/new/",                          v.create_client,      name="create_client"),
    path("clients/<uuid:client_id>/",             v.client_detail,      name="client_detail"),
    path("clients/<uuid:client_id>/credits/add/", v.add_credits_view,   name="add_credits"),
    path("clients/<uuid:client_id>/suspend/",     v.suspend_client_view,name="suspend_client"),
    path("credits/",                              v.credits_overview,   name="credits_overview"),
    path("activity/",                             v.activity_log,       name="activity_log"),
    path("settings/",                             v.settings_view,      name="settings"),
]
