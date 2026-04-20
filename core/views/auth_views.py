# core/views/auth_views.py
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from ..forms import LoginForm


def login_view(request):
    if request.user.is_authenticated:
        return _redirect_by_role(request.user)
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["email"],
            password=form.cleaned_data["password"],
        )
        if user and user.is_active:
            login(request, user)
            return _redirect_by_role(user)
        messages.error(request, "Invalid email or password.")
    return render(request, "auth/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("login")


def _redirect_by_role(user):
    if user.is_super_admin:
        return redirect("superadmin:dashboard")
    return redirect("client:dashboard")
