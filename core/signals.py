# core/signals.py
"""
Post-save signals. Registered via CoreConfig.ready().
Keep these lightweight — heavy work belongs in tasks.py.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.signals import user_logged_in
from .models import ActivityLog


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    ActivityLog.objects.create(
        company    = user.company if hasattr(user, "company") else None,
        actor      = user,
        event_type = ActivityLog.EventType.USER_LOGIN,
        detail     = f"Login from {request.META.get('REMOTE_ADDR', 'unknown')}",
    )
