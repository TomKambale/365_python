import logging

from django.apps import AppConfig

log = logging.getLogger("azure-admin")


class ApiConfig(AppConfig):
    name = "api"

    def ready(self):
        from api import config

        log.info("=== AZURE ADMIN API (Django) STARTING ===")
        log.info(
            f"Azure credentials: {'✅ Found' if config.HAS_AZURE_CREDENTIALS else '❌ Not found'}"
        )
