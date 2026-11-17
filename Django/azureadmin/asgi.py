"""ASGI entrypoint. Run with:  uvicorn azureadmin.asgi:application --port 5000"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "azureadmin.settings")

application = get_asgi_application()
