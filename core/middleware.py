# core/middleware.py
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.conf import settings


class MaintenanceFreezeMiddleware:
    """
    Blocks payslip send attempts between Dec 26-31 with a clear UI message.
    Only applies to the send confirmation URL; everything else works normally.
    """
    BLOCKED_URL_NAMES = ["client:send_step3"]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.urls import resolve, Resolver404
        from .secure_utils import is_maintenance_freeze

        if request.method == "POST" and is_maintenance_freeze():
            try:
                match = resolve(request.path_info)
                url_name = f"{match.namespace}:{match.url_name}" if match.namespace else match.url_name
                if url_name in self.BLOCKED_URL_NAMES:
                    from django.contrib import messages
                    messages.error(
                        request,
                        "⚠️ FixSeen is in maintenance mode from December 26–31. "
                        "Payslip sending is disabled. Please try again in January."
                    )
                    return redirect("client:dashboard")
            except Exception:
                pass

        return self.get_response(request)


class SuspendedAccountMiddleware:
    """
    Redirects suspended HR users to the suspended page on every request.
    Allows logout and static files through.
    """
    EXEMPT_PATHS = ["/logout/", "/login/", "/static/", "/media/"]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and hasattr(request.user, "is_suspended")
            and request.user.is_suspended
            and not any(request.path.startswith(p) for p in self.EXEMPT_PATHS)
            and request.path != reverse("client:suspended")
        ):
            return redirect("client:suspended")
        return self.get_response(request)
