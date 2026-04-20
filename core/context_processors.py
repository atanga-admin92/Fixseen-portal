# core/context_processors.py
from .secure_utils import is_maintenance_freeze


def global_context(request):
    """
    Injects portal-wide context into every template.
    Available in all templates as {{ available_credits }}, {{ maintenance_freeze }}, etc.
    """
    ctx = {
        "maintenance_freeze": is_maintenance_freeze(),
        "available_credits":  0,
        "account_status":     None,
        "company":            None,
    }
    if request.user.is_authenticated and hasattr(request.user, "company") and request.user.company:
        ctx["available_credits"] = request.user.available_credits
        ctx["account_status"]    = request.user.account_status
        ctx["company"]           = request.user.company
    return ctx
