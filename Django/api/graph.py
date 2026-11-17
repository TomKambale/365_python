"""Microsoft Graph plumbing: token cache, shared HTTP client, throttled calls.

Ported from main.py. One Django-specific change: the httpx client and the
asyncio semaphores are kept *per event loop*. Under uvicorn (the recommended
way to run this) there is a single loop, so behavior is identical to FastAPI.
Under ``manage.py runserver`` each request may get its own loop; keying by
loop prevents "attached to a different event loop" errors.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from api import config
from api.exceptions import ApiError

log = logging.getLogger("azure-admin")

# ===== token cache (plain strings — safe to share across loops) =====
_access_token: Optional[str] = None
_token_expiry: Optional[datetime] = None

# ===== per-event-loop state =====
_loop_state: dict[int, dict] = {}


def _state() -> dict:
    loop = asyncio.get_running_loop()
    st = _loop_state.get(id(loop))
    if st is None:
        st = {
            "client": httpx.AsyncClient(
                # Fail fast on connect (unreachable host) but allow time for reads.
                timeout=httpx.Timeout(connect=4.0, read=20.0, write=10.0, pool=4.0),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            ),
            "graph_sem": asyncio.Semaphore(config.GRAPH_CONCURRENCY),
            "bulk_sem": asyncio.Semaphore(config.BULK_ADD_CONCURRENCY),
        }
        _loop_state[id(loop)] = st
    return st


def get_http_client() -> httpx.AsyncClient:
    return _state()["client"]


def bulk_semaphore() -> asyncio.Semaphore:
    """Bounds parallel Graph writes from the bulk member upload."""
    return _state()["bulk_sem"]


async def get_access_token() -> Optional[str]:
    global _access_token, _token_expiry
    url = f"https://login.microsoftonline.com/{config.AZURE_TENANT_ID}/oauth2/v2.0/token"
    payload = {
        "client_id": config.AZURE_CLIENT_ID,
        "client_secret": config.AZURE_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url, data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
        data = resp.json()
        _access_token = data["access_token"]
        _token_expiry = datetime.utcnow() + timedelta(seconds=data["expires_in"])
        log.info("✅ Graph API token obtained")
        return _access_token
    except httpx.HTTPStatusError as e:
        log.error(f"❌ Failed to get access token: {e}")
        log.error(f"Status: {e.response.status_code}")
        log.error(f"Response body: {e.response.text}")
        try:
            body = e.response.json()
            if "error_description" in body:
                log.error(f"👉 REASON: {body['error_description']}")
        except Exception:
            pass
        return None
    except Exception as e:
        log.error(f"❌ Failed to get access token: {e}")
        return None


async def get_token() -> Optional[str]:
    if not _access_token or not _token_expiry or datetime.utcnow() >= _token_expiry:
        token = await get_access_token()
        if token:
            log.info("🚀 Token Acquired")
        return token
    return _access_token


async def call_graph_api(endpoint: str, method: str = "GET", data: Any = None) -> dict:
    token = await get_token()
    if not token:
        raise ApiError(500, "No access token available")
    url = f"https://graph.microsoft.com/v1.0{endpoint}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        st = _state()
        async with st["graph_sem"]:
            resp = await st["client"].request(method, url, headers=headers, json=data)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()
    except httpx.HTTPStatusError as e:
        log.error(f"Graph API error for {endpoint}: {e}")
        log.error(f"Graph body: {e.response.text}")
        raise ApiError(e.response.status_code, e.response.text)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        # Network/DNS/proxy problem reaching graph.microsoft.com — surface a
        # clean 502 instead of an unhandled 500 traceback.
        log.error(f"❌ Cannot reach Graph API for {endpoint}: {type(e).__name__}: {e}")
        raise ApiError(502, {
            "error": "Cannot reach Microsoft Graph",
            "hint": "Check DNS/VPN/proxy for graph.microsoft.com from this host.",
            "cause": f"{type(e).__name__}: {e}",
        })
    except httpx.HTTPError as e:
        log.error(f"❌ Graph request failed for {endpoint}: {type(e).__name__}: {e}")
        raise ApiError(502, {"error": "Graph request failed", "cause": f"{type(e).__name__}: {e}"})


async def graph_request_with_retry(
    endpoint: str,
    method: str = "GET",
    data: Any = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> dict:
    """Like call_graph_api, but retries Graph throttling (429) and transient 5xx.

    Exponential backoff with jitter. Built for the bulk member endpoint, which
    can issue many writes in a burst. Non-retryable responses (4xx other than
    429) are re-raised immediately.
    """
    attempt = 0
    while True:
        try:
            return await call_graph_api(endpoint, method, data)
        except ApiError as e:
            retryable = e.status_code == 429 or 500 <= e.status_code < 600
            if not retryable or attempt >= max_retries:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            log.warning(
                f"Graph {e.status_code} on {method} {endpoint} — retry "
                f"{attempt + 1}/{max_retries} in {delay:.1f}s"
            )
            await asyncio.sleep(delay)
            attempt += 1
