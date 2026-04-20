# config/settings/development.py
from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]


# Django Debug Toolbar (optional — pip install django-debug-toolbar)
INTERNAL_IPS = ["127.0.0.1"]
