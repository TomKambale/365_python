import logging
from asyncio import iscoroutinefunction

from django.utils.decorators import sync_and_async_middleware

log = logging.getLogger("azure-admin")


@sync_and_async_middleware
def request_logging_middleware(get_response):
    """Port of the FastAPI log_requests middleware."""
    if iscoroutinefunction(get_response):
        async def middleware(request):
            log.info(f"➡️ {request.method} {request.path}")
            return await get_response(request)
    else:
        def middleware(request):
            log.info(f"➡️ {request.method} {request.path}")
            return get_response(request)
    return middleware
