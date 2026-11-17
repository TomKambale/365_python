#!/bin/bash
# Run the Django port under ASGI (single event loop — required for the shared
# httpx client and asyncio semaphores to behave like the FastAPI original).
cd "$(dirname "$0")"
source ../venv/bin/activate
exec uvicorn azureadmin.asgi:application --host 0.0.0.0 --port "${PORT:-5000}"
