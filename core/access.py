# core/access.py
from functools import wraps
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages


def super_admin_required(view_func):
    @wraps(view_func)
    @login_required(login_url="login")
    def wrapper(request, *args, **kwargs):
        if not request.user.is_super_admin:
            messages.error(request, "Access denied.")
            return redirect("client:dashboard")
        return view_func(request, *args, **kwargs)
    return wrapper


def client_required(view_func):
    @wraps(view_func)
    @login_required(login_url="login")
    def wrapper(request, *args, **kwargs):
        if not request.user.is_hr_admin:
            return redirect("superadmin:dashboard")
        if request.user.is_suspended:
            return redirect("client:suspended")
        return view_func(request, *args, **kwargs)
    return wrapper
