"""Django settings for the Azure Admin API (port of the FastAPI main.py)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent          # .../Django
PROJECT_ROOT = BASE_DIR.parent                             # repo root

# Reuse the repo-root .env (same one the FastAPI app uses); a Django-local
# .env may override it.
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "corsheaders",
    "api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "api.middleware.request_logging_middleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "azureadmin.urls"
WSGI_APPLICATION = "azureadmin.wsgi.application"
ASGI_APPLICATION = "azureadmin.asgi.application"

TEMPLATES = []

# No models — the app is a stateless proxy over Microsoft Graph. The sqlite
# default is never touched unless someone adds models and migrates.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# The API routes (ported from FastAPI) have no trailing slashes.
APPEND_SLASH = False

# CORS — the FastAPI app allowed "*" plus credentialed localhost origins.
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = ["content-type", "authorization"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
