# config/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from core.views.auth_views import login_view, logout_view

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("",               login_view,  name="login"),
    path("logout/",        logout_view, name="logout"),
    path("admin/",         include("core.urls_superadmin", namespace="superadmin")),
    path("portal/",        include("core.urls_client",     namespace="client")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
