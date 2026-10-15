import os
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Any
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

# ========== ENVIRONMENT SETUP ==========
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("azure-admin")
try:
    from email_service import email_service
    log.info("✅ Email service loaded")
except Exception as e:
    log.warning(f"⚠️ Email service not available: {e}")
    email_service = None

AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "").strip()
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "").strip()
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "").strip()

HAS_AZURE_CREDENTIALS = all([AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_CLIENT_SECRET])

log.info("=== AZURE ADMIN API STARTING ===")
log.info(f"Azure credentials: {'✅ Found' if HAS_AZURE_CREDENTIALS else '❌ Not found'}")

# ========== APP ==========
app = FastAPI(title="Azure Admin API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# ========== GRAPH TOKEN MANAGEMENT ==========
_access_token: Optional[str] = None
_token_expiry: Optional[datetime] = None


async def get_access_token() -> Optional[str]:
    global _access_token, _token_expiry
    url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    payload = {
        "client_id": AZURE_CLIENT_ID,
        "client_secret": AZURE_CLIENT_SECRET,
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
        raise HTTPException(status_code=500, detail="No access token available")
    url = f"https://graph.microsoft.com/v1.0{endpoint}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        client = get_http_client()
        async with _graph_semaphore:
            resp = await client.request(method, url, headers=headers, json=data)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()
    except httpx.HTTPStatusError as e:
        log.error(f"Graph API error for {endpoint}: {e}")
        log.error(f"Graph body: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        # Network/DNS/proxy problem reaching graph.microsoft.com — surface a
        # clean 502 instead of an unhandled 500 traceback.
        log.error(f"❌ Cannot reach Graph API for {endpoint}: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=502,
            detail={
                "error": "Cannot reach Microsoft Graph",
                "hint": "Check DNS/VPN/proxy for graph.microsoft.com from this host.",
                "cause": f"{type(e).__name__}: {e}",
            },
        )
    except httpx.HTTPError as e:
        log.error(f"❌ Graph request failed for {endpoint}: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "Graph request failed", "cause": f"{type(e).__name__}: {e}"},
        )


# ========== HELPERS ==========
def format_user(user: dict) -> dict:
    upn = user.get("userPrincipalName", "")
    return {
        "accountEnabled": user.get("accountEnabled"),
        "displayName": user.get("displayName"),
        "mailNickname": user.get("mailNickname") or upn.split("@")[0] if upn else None,
        "userPrincipalName": upn,
        "usageLocation": user.get("usageLocation") or "US",
        "id": user.get("id"),
        "mail": user.get("mail"),
        "givenName": user.get("givenName"),
        "surname": user.get("surname"),
        "jobTitle": user.get("jobTitle"),
        "department": user.get("department"),
        "mobilePhone": user.get("mobilePhone"),
        "officeLocation": user.get("officeLocation"),
        "preferredLanguage": user.get("preferredLanguage"),
    }


def format_group(group: dict) -> dict:
    return {
        "id": group.get("id"),
        "displayName": group.get("displayName"),
        "description": group.get("description"),
        "mailEnabled": group.get("mailEnabled"),
        "securityEnabled": group.get("securityEnabled"),
        "createdDateTime": group.get("createdDateTime"),
        "mail": group.get("mail"),
        "groupTypes": group.get("groupTypes") or [],
    }


# ========== TTU DOMAIN / ROLE HELPERS ==========
STUDENT_DOMAIN = "@students.ttu.ac.ke"
STAFF_DOMAIN = "@ttu.ac.ke"


def get_role(email: str) -> Optional[str]:
    """Return 'student', 'staff', or None for an invalid domain.

    NOTE: Student domain must be checked FIRST because '@students.ttu.ac.ke'
    also ends with '@ttu.ac.ke'."""
    if not email:
        return None
    email = email.strip().lower()
    if email.endswith(STUDENT_DOMAIN):
        return "student"
    if email.endswith(STAFF_DOMAIN):
        return "staff"
    return None


def require_role(email: str, role: str) -> str:
    """Validate the email has the expected role; raise HTTPException otherwise."""
    actual = get_role(email)
    if actual is None:
        raise HTTPException(status_code=400, detail={"error": "Invalid TTU email domain"})
    if actual != role:
        raise HTTPException(
            status_code=403,
            detail={"error": f"This action requires a {role} account", "role": actual},
        )
    return email.strip().lower()


async def resolve_user_id_by_email(email: str) -> str:
    """Resolve a UPN/email to a directory user id."""
    if not HAS_AZURE_CREDENTIALS:
        return f"mock-user-{email.split('@')[0]}"
    try:
        user = await call_graph_api(f"/users/{email}?$select=id,userPrincipalName,displayName,mail")
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail={"error": "User not found in directory", "email": email})
        raise
    return user["id"]


# ========== MOCK DATA ==========
mock_users: list[dict] = [
    {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "displayName": "John Doe",
        "userPrincipalName": "john.doe@contoso.com",
        "mail": "john.doe@contoso.com",
        "jobTitle": "Senior Developer",
        "department": "Engineering",
        "accountEnabled": True,
        "createdDateTime": "2024-01-15T10:30:00Z",
    },
    {
        "id": "550e8400-e29b-41d4-a716-446655440002",
        "displayName": "Jane Smith",
        "userPrincipalName": "jane.smith@contoso.com",
        "mail": "jane.smith@contoso.com",
        "jobTitle": "Product Manager",
        "department": "Product",
        "accountEnabled": True,
        "createdDateTime": "2024-01-20T14:45:00Z",
    },
]

mock_groups: list[dict] = [
    {
        "id": "550e8400-e29b-41d4-a716-446655440101",
        "displayName": "IT Department",
        "description": "Information Technology Group",
        "mailEnabled": True,
        "securityEnabled": True,
        "createdDateTime": "2024-01-10T08:00:00Z",
        "mail": "itdept@contoso.com",
    },
    {
        "id": "550e8400-e29b-41d4-a716-446655440102",
        "displayName": "Engineering Team",
        "description": "Software Development and Engineering",
        "mailEnabled": False,
        "securityEnabled": True,
        "createdDateTime": "2024-01-12T09:30:00Z",
    },
]

mock_licenses: list[dict] = [
    {
        "id": "ENTERPRISEPACK",
        "skuId": "6fd2c87f-b296-42f0-b197-1e91e994b900",
        "skuPartNumber": "ENTERPRISEPACK",
        "appliesTo": "User",
        "productName": "Office 365 E3",
        "totalUnits": 100,
        "consumedUnits": 45,
        "availableUnits": 55,
    },
    {
        "id": "EMS",
        "skuId": "efccb6f7-5641-4e0e-bd10-b4976e1bf68e",
        "skuPartNumber": "EMS",
        "appliesTo": "User",
        "productName": "Enterprise Mobility + Security E3",
        "totalUnits": 50,
        "consumedUnits": 20,
        "availableUnits": 30,
    },
]

# LICENSE_SKU_MAP = {
#     "Student": {"skuId": "314c4481-f395-4525-be8b-2ec4bb1e9d91", "displayName": "Microsoft 365 A1 for Students"},
#     "Faculty": {"skuId": "94763226-9b3c-4e75-a931-5c89701abe66", "displayName": "Microsoft 365 A3 for Faculty"},
# }
LICENSE_SKU_MAP = {
    "Student": {
        "displayName": "Microsoft 365 A1 for Students",
        "skus": [
            {
                "skuId": "314c4481-f395-4525-be8b-2ec4bb1e9d91",  # ← real GUID from your tenant
                "skuPartNumber": "STANDARDWOFFPACK_STUDENT",
                "label": "M365 A1 Student",
                "required": True,
            },
        ],
    },
    "Faculty": {
        "displayName": "Microsoft 365 A3 for Faculty (bundled)",
        "skus": [
            {
                "skuId": "94763226-9b3c-4e75-a931-5c89701abe66",  # ← real GUID
                "skuPartNumber": "STANDARDWOFFPACK_FACULTY",
                "label": "M365 A3 Faculty",
                "required": True,
            },
            {
                "skuId": "a403ebcc-fae0-4ca2-8c8c-7a907fd6c235",  # ← real GUID
                "skuPartNumber": "POWER_BI_STANDARD",
                "label": "Power BI",
                "required": False,   # optional: don't fail the whole thing if this fails
            },
            {
                "skuId": "fcecd1f9-a91e-488d-a918-a96cdb6ce2b",
                "skuPartNumber": "AX7_USER_TRIAL",
                "label": "AX7",
                "required": False,
            },
            {
                "skuId": "f30db892-07e9-47e9-837c-80727f46fd3d",  # ← real GUID
                "skuPartNumber": "FLOW_FREE",
                "label": "Flow Free",
                "required": False,   # optional: don't fail the whole thing if this fails
            },
        ],
    },
}

# ========== REQUEST LOGGING MIDDLEWARE ==========
@app.middleware("http")
async def log_requests(request: Request, call_next):
    log.info(f"➡️ {request.method} {request.url.path}")
    response = await call_next(request)
    return response


# ========== CACHE & CONCURRENCY UTILITIES ==========
# A tiny in-process TTL cache + a shared HTTP client. These cut the flood of
# identical Graph calls the group pages were generating on every load.
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    """Return a shared AsyncClient so we reuse TCP/TLS connections to Graph."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            # Fail fast on connect (unreachable host) but allow time for reads.
            timeout=httpx.Timeout(connect=4.0, read=20.0, write=10.0, pool=4.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _http_client


# Simple TTL caches. Group lists change rarely → longer TTL. Membership changes
# on join/leave → invalidated explicitly via cache_invalidate().
GROUPS_CACHE_TTL = int(os.getenv("GROUPS_CACHE_TTL", "60"))       # seconds
MEMBERS_CACHE_TTL = int(os.getenv("MEMBERS_CACHE_TTL", "45"))     # seconds
OWNERS_CACHE_TTL = int(os.getenv("OWNERS_CACHE_TTL", "120"))      # seconds
USER_ID_CACHE_TTL = int(os.getenv("USER_ID_CACHE_TTL", "600"))    # seconds

_cache: dict[str, tuple[float, Any]] = {}
_cache_locks: dict[str, asyncio.Lock] = {}

# Max simultaneous Graph requests — protects the tenant & the local process.
GRAPH_CONCURRENCY = int(os.getenv("GRAPH_CONCURRENCY", "8"))
_graph_semaphore = asyncio.Semaphore(GRAPH_CONCURRENCY)


def cache_get(key: str, ttl: int):
    """Return the cached value for key if still within ttl, else None."""
    entry = _cache.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return value


def cache_set(key: str, value: Any):
    _cache[key] = (time.time(), value)


def cache_invalidate(*prefixes: str):
    """Drop every cache entry whose key starts with any given prefix."""
    if not prefixes:
        _cache.clear()
        return
    for k in list(_cache.keys()):
        if any(k.startswith(p) for p in prefixes):
            _cache.pop(k, None)


async def cached_call(key: str, ttl: int, producer):
    """TTL cache with per-key locking to collapse duplicate concurrent misses.

    If several requests miss the same key at once, only ONE upstream call is
    made; the others await the same result (request coalescing)."""
    hit = cache_get(key, ttl)
    if hit is not None:
        return hit

    lock = _cache_locks.setdefault(key, asyncio.Lock())
    async with lock:
        # Re-check after acquiring the lock (another task may have filled it).
        hit = cache_get(key, ttl)
        if hit is not None:
            return hit
        value = await producer()
        cache_set(key, value)
        return value


# ========== PYDANTIC MODELS ==========
class SelfRegisterRequest(BaseModel):
    email: str
    firstName: str
    lastName: str
    displayName: str
    licenseType: str
    password: str


class BulkRequest(BaseModel):
    operation: str
    userIds: list[str]
    data: Optional[dict] = None


class VerifyEmailRequest(BaseModel):
    email: EmailStr


class GroupActionRequest(BaseModel):
    email: EmailStr


class StaffGroupRequest(BaseModel):
    email: EmailStr
    displayName: str
    description: Optional[str] = ""
    mailNickname: Optional[str] = None


# ========== HEALTH & STATS ==========
@app.get("/api/health")
async def health():
    return {
        "status": "OK",
        "service": "Azure Admin API",
        "timestamp": datetime.utcnow().isoformat(),
        "usingGraphAPI": HAS_AZURE_CREDENTIALS,
        "version": "1.0.0",
    }


@app.get("/api/stats")
async def stats():
    user_count = group_count = license_count = 0
    if HAS_AZURE_CREDENTIALS:
        try:
            users = await call_graph_api("/users?$count=true&$top=1")
            user_count = len(users.get("value", []))
            groups = await call_graph_api("/groups?$count=true&$top=1")
            group_count = len(groups.get("value", []))
            licenses = await call_graph_api("/subscribedSkus")
            license_count = len(licenses.get("value", []))
        except Exception:
            user_count, group_count, license_count = (
                len(mock_users), len(mock_groups), len(mock_licenses),
            )
    else:
        user_count, group_count, license_count = (
            len(mock_users), len(mock_groups), len(mock_licenses),
        )
    return {
        "users": user_count,
        "groups": group_count,
        "licenses": license_count,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ========== EMAIL / ROLE VERIFICATION ==========
@app.post("/api/verify-email")
async def verify_email(req: VerifyEmailRequest):
    """
    Simplified TTU role verification.

    NOTE: This only checks the DOMAIN of the supplied email — it does NOT prove
    the caller owns the address. In production, replace this with an OAuth 2.0 /
    OpenID Connect flow (e.g. Google Sign-In or Entra ID) and derive the role
    from the verified ID token claims.
    """
    email = req.email.strip().lower()
    role = get_role(email)
    if role is None:
        raise HTTPException(status_code=400, detail={"error": "Invalid TTU email domain"})

    # Best-effort: confirm the account exists in the directory.
    exists = None
    if HAS_AZURE_CREDENTIALS:
        try:
            existing = await call_graph_api(
                f"/users/{email}?$select=id,displayName,userPrincipalName"
            )
            exists = True
        except HTTPException as e:
            if e.status_code == 404:
                exists = False
            else:
                exists = None

    log.info(f"verify-email: {email} -> role={role} (exists={exists})")
    return {"email": email, "role": role, "existsInDirectory": exists}


# ========== USERS ==========
@app.get("/api/users")
async def list_users():
    if HAS_AZURE_CREDENTIALS:
        query = (
            "/users?$select=id,displayName,userPrincipalName,mail,jobTitle,"
            "department,accountEnabled,createdDateTime,mobilePhone,"
            "officeLocation,usageLocation,userType&$top=999"
        )
        users = await call_graph_api(query)
        value = [format_user(u) for u in users.get("value", [])]
        return {"value": value, "count": len(value), "total": len(value),
                "timestamp": datetime.utcnow().isoformat()}
    return {"value": mock_users, "count": len(mock_users),
            "total": len(mock_users), "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    if HAS_AZURE_CREDENTIALS:
        user = await call_graph_api(f"/users/{user_id}")
        return format_user(user)
    for u in mock_users:
        if u["id"] == user_id:
            return u
    raise HTTPException(status_code=404, detail="User not found")


@app.post("/api/users", status_code=201)
async def create_user(user_data: dict = Body(...)):
    if HAS_AZURE_CREDENTIALS:
        new_user = await call_graph_api("/users", "POST", user_data)
        return format_user(new_user)
    new_user = {
        "id": f"mock-{int(datetime.utcnow().timestamp() * 1000)}",
        **user_data,
        "createdDateTime": datetime.utcnow().isoformat(),
        "accountEnabled": True,
    }
    mock_users.append(new_user)
    return new_user


@app.put("/api/users/{user_id}")
async def update_user(user_id: str, updates: dict = Body(...)):
    allowed = {
        "displayName", "givenName", "surname", "mailNickname", "jobTitle",
        "department", "mobilePhone", "officeLocation", "usageLocation",
        "preferredLanguage", "accountEnabled",
    }
    sanitized = {k: v for k, v in updates.items() if k in allowed}
    log.info(f"PUT /api/users/{user_id} - sanitized keys: {list(sanitized.keys())}")

    if HAS_AZURE_CREDENTIALS:
        updated = await call_graph_api(f"/users/{user_id}", "PATCH", sanitized)
        return format_user(updated) if updated else {"message": "updated"}
    for u in mock_users:
        if u["id"] == user_id:
            u.update(sanitized)
            return u
    raise HTTPException(status_code=404, detail="User not found")


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: str):
    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/users/{user_id}", "DELETE")
        return {"message": "User deleted successfully"}
    for i, u in enumerate(mock_users):
        if u["id"] == user_id:
            mock_users.pop(i)
            return {"message": "User deleted successfully"}
    raise HTTPException(status_code=404, detail="User not found")


@app.post("/api/users/bulk")
async def bulk_users(req: BulkRequest):
    results = []
    if HAS_AZURE_CREDENTIALS:
        for uid in req.userIds[:10]:
            try:
                if req.operation == "enable":
                    await call_graph_api(f"/users/{uid}", "PATCH", {"accountEnabled": True})
                    results.append({"userId": uid, "status": "enabled"})
                elif req.operation == "disable":
                    await call_graph_api(f"/users/{uid}", "PATCH", {"accountEnabled": False})
                    results.append({"userId": uid, "status": "disabled"})
                elif req.operation == "update":
                    await call_graph_api(f"/users/{uid}", "PATCH", req.data or {})
                    results.append({"userId": uid, "status": "updated"})
            except Exception as e:
                results.append({"userId": uid, "status": "failed", "error": str(e)})
    else:
        for uid in req.userIds:
            for u in mock_users:
                if u["id"] == uid:
                    if req.operation == "enable":
                        u["accountEnabled"] = True
                        results.append({"userId": uid, "status": "enabled"})
                    elif req.operation == "disable":
                        u["accountEnabled"] = False
                        results.append({"userId": uid, "status": "disabled"})
                    elif req.operation == "update":
                        u.update(req.data or {})
                        results.append({"userId": uid, "status": "updated"})
    return {"operation": req.operation, "processed": len(results),
            "results": results, "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/users/import")
async def import_users(body: dict = Body(...)):
    users = body.get("users", [])
    if not isinstance(users, list):
        raise HTTPException(status_code=400, detail="Expected array of users")
    results = []
    if HAS_AZURE_CREDENTIALS:
        for u in users[:5]:
            try:
                new_user = await call_graph_api("/users", "POST", {
                    "accountEnabled": True,
                    "displayName": u["displayName"],
                    "userPrincipalName": u["userPrincipalName"],
                    "mailNickname": u["userPrincipalName"].split("@")[0],
                    "passwordProfile": {
                        "forceChangePasswordNextSignIn": True,
                        "password": "TempPassword123!",
                    },
                })
                results.append({"userPrincipalName": u["userPrincipalName"],
                                "status": "created", "id": new_user["id"]})
            except Exception as e:
                results.append({"userPrincipalName": u["userPrincipalName"],
                                "status": "failed", "error": str(e)})
    else:
        for u in users:
            mock_users.append({
                "id": f"imported-{int(datetime.utcnow().timestamp() * 1000)}",
                **u, "accountEnabled": True,
                "createdDateTime": datetime.utcnow().isoformat(),
            })
            results.append({"userPrincipalName": u["userPrincipalName"],
                            "status": "created"})
    return {"total": len(users), "processed": len(results),
            "results": results, "timestamp": datetime.utcnow().isoformat()}


# ========== GROUPS (STAFF-LED MANAGEMENT) ==========
UNIFIED_FILTER = "/groups?$filter=groupTypes/any(c:c eq 'Unified')&$top=999&$select=id,displayName,description,mail,visibility,createdDateTime,mailEnabled,securityEnabled,groupTypes"


async def _fetch_unified_groups() -> list[dict]:
    """Fetch all Microsoft 365 (Unified) groups, formatted."""
    if HAS_AZURE_CREDENTIALS:
        try:
            groups = await call_graph_api(UNIFIED_FILTER)
        except HTTPException as e:
            # Only skip the fallback for OUR "cannot reach Graph" 502 — that
            # failure hits every endpoint, so retrying another URL is pointless.
            # Any other error (e.g. complex $filter rejected, or a transient
            # 5xx from Graph) MUST still fall back to the plain /groups list so
            # students keep seeing groups.
            if e.status_code == 502:
                raise
            log.warning(f"Unified $filter failed ({e.detail}); falling back to /groups")
            groups = await call_graph_api("/groups?$top=999&$select=id,displayName,description,mail,visibility,createdDateTime,mailEnabled,securityEnabled,groupTypes")
        value = [format_group(g) for g in groups.get("value", [])
                 if "Unified" in (g.get("groupTypes") or [])]
        for g, raw in zip(value, [x for x in groups.get("value", []) if "Unified" in (x.get("groupTypes") or [])]):
            g["visibility"] = raw.get("visibility")
        return value
    # Mock mode: only return Unified groups
    return [g for g in mock_groups if "Unified" in (g.get("groupTypes") or [])]


async def _is_group_owner(group_id: str, user_id: str) -> bool:
    """Return True if user_id is an owner of group_id."""
    if not HAS_AZURE_CREDENTIALS:
        for g in mock_groups:
            if g["id"] == group_id:
                return user_id in g.get("owners", [])
        return False
    try:
        owners = await call_graph_api(f"/groups/{group_id}/owners?$select=id")
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail="Group not found")
        raise
    return any(o.get("id") == user_id for o in owners.get("value", []))


@app.get("/api/groups")
async def list_groups():
    """List all Microsoft 365 (Unified) groups."""
    value = await _fetch_unified_groups()
    return {"value": value, "count": len(value), "total": len(value),
            "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/groups/owned")
async def list_owned_groups(email: EmailStr):
    """List Unified groups owned by the given staff email."""
    require_role(str(email), "staff")
    user_id = await resolve_user_id_by_email(str(email))
    all_groups = await _fetch_unified_groups()

    owned = []
    if HAS_AZURE_CREDENTIALS:
        for g in all_groups:
            try:
                if await _is_group_owner(g["id"], user_id):
                    owned.append(g)
            except HTTPException:
                continue
    else:
        for g in all_groups:
            if user_id in g.get("owners", []):
                owned.append(g)
    return {"value": owned, "count": len(owned),
            "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/groups", status_code=201)
async def create_group(req: StaffGroupRequest):
    """Staff-only: create a Unified group and set the creator as owner."""
    staff_email = require_role(str(req.email), "staff")

    display_name = (req.displayName or "").strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="displayName is required")

    mail_nickname = (req.mailNickname or "").strip()
    if not mail_nickname:
        mail_nickname = "".join(ch for ch in display_name.lower() if ch.isalnum())[:64] or "group"

    payload = {
        "displayName": display_name,
        "description": req.description or f"Created by {staff_email}",
        "groupTypes": ["Unified"],
        "mailEnabled": True,
        "securityEnabled": False,
        "mailNickname": mail_nickname,
    }

    if HAS_AZURE_CREDENTIALS:
        created = await call_graph_api("/groups", "POST", payload)
        group_id = created["id"]

        # Add the creator as an owner
        try:
            user_id = await resolve_user_id_by_email(staff_email)
            await call_graph_api(f"/groups/{group_id}/owners/$ref", "POST", {
                "@odata.id": f"https://graph.microsoft.com/v1.0/users/{user_id}"
            })
        except HTTPException as e:
            log.error(f"Failed to set owner on group {group_id}: {e.detail}")

        log.info(f"AUDIT group_created id={group_id} name='{display_name}' by={staff_email}")
        return format_group(created)

    # Mock mode
    user_id = await resolve_user_id_by_email(staff_email)
    new_group = {
        "id": f"mock-group-{int(datetime.utcnow().timestamp() * 1000)}",
        **payload,
        "createdDateTime": datetime.utcnow().isoformat(),
        "groupTypes": ["Unified"],
        "owners": [user_id],
        "members": [user_id],
    }
    mock_groups.append(new_group)
    log.info(f"AUDIT group_created (mock) name='{display_name}' by={staff_email}")
    return format_group(new_group)


@app.get("/api/groups/{group_id}")
async def get_group(group_id: str):
    if HAS_AZURE_CREDENTIALS:
        return format_group(await call_graph_api(
            f"/groups/{group_id}?$select=id,displayName,description,mail,visibility,createdDateTime,mailEnabled,securityEnabled,groupTypes"
        ))
    for g in mock_groups:
        if g["id"] == group_id:
            return g
    raise HTTPException(status_code=404, detail="Group not found")


@app.put("/api/groups/{group_id}")
async def update_group(group_id: str, updates: dict = Body(...)):
    """Staff-owner-only: update group metadata."""
    email = updates.pop("email", None)
    if not email:
        raise HTTPException(status_code=400, detail="email is required to authorize this action")
    staff_email = require_role(str(email), "staff")
    user_id = await resolve_user_id_by_email(staff_email)
    if not await _is_group_owner(group_id, user_id):
        raise HTTPException(status_code=403, detail="Only a group owner may update this group")

    allowed = {"displayName", "description", "visibility"}
    sanitized = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not sanitized:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    if HAS_AZURE_CREDENTIALS:
        updated = await call_graph_api(f"/groups/{group_id}", "PATCH", sanitized)
        log.info(f"AUDIT group_updated id={group_id} fields={list(sanitized)} by={staff_email}")
        return format_group(updated) if updated else {"id": group_id, "message": "updated", **sanitized}
    for g in mock_groups:
        if g["id"] == group_id:
            g.update(sanitized)
            log.info(f"AUDIT group_updated (mock) id={group_id} by={staff_email}")
            return g
    raise HTTPException(status_code=404, detail="Group not found")


@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: str, email: Optional[str] = None, body: dict = Body(default={})):
    """Staff-owner-only: delete a group."""
    requester = email or body.get("email")
    if not requester:
        raise HTTPException(status_code=400, detail="email is required to authorize this action")
    staff_email = require_role(str(requester), "staff")
    user_id = await resolve_user_id_by_email(staff_email)

    if not await _is_group_owner(group_id, user_id):
        raise HTTPException(status_code=403, detail="Only a group owner may delete this group")

    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}", "DELETE")
        log.info(f"AUDIT group_deleted id={group_id} by={staff_email}")
        return {"message": "Group deleted successfully"}
    for i, g in enumerate(mock_groups):
        if g["id"] == group_id:
            mock_groups.pop(i)
            log.info(f"AUDIT group_deleted (mock) id={group_id} by={staff_email}")
            return {"message": "Group deleted successfully"}
    raise HTTPException(status_code=404, detail="Group not found")


@app.get("/api/groups/{group_id}/members")
async def get_group_members(group_id: str):
    """List the members of a group."""
    if HAS_AZURE_CREDENTIALS:
        members = await call_graph_api(
            f"/groups/{group_id}/members?$select=id,displayName,userPrincipalName,mail"
        )
        value = [{
            "id": m.get("id"),
            "displayName": m.get("displayName"),
            "userPrincipalName": m.get("userPrincipalName"),
            "mail": m.get("mail"),
        } for m in members.get("value", [])]
        return {"value": value, "count": len(value),
                "timestamp": datetime.utcnow().isoformat()}
    for g in mock_groups:
        if g["id"] == group_id:
            ids = g.get("members", [])
            value = [{"id": uid, "userPrincipalName": f"{uid.replace('mock-user-', '')}@ttu.ac.ke"}
                     for uid in ids]
            return {"value": value, "count": len(value),
                    "timestamp": datetime.utcnow().isoformat()}
    raise HTTPException(status_code=404, detail="Group not found")


@app.get("/api/groups/{group_id}/owners")
async def get_group_owners(group_id: str):
    """List the owners of a group."""
    if HAS_AZURE_CREDENTIALS:
        owners = await call_graph_api(
            f"/groups/{group_id}/owners?$select=id,displayName,userPrincipalName,mail"
        )
        value = [{
            "id": o.get("id"),
            "displayName": o.get("displayName"),
            "userPrincipalName": o.get("userPrincipalName"),
            "mail": o.get("mail"),
        } for o in owners.get("value", [])]
        return {"value": value, "count": len(value),
                "timestamp": datetime.utcnow().isoformat()}
    for g in mock_groups:
        if g["id"] == group_id:
            ids = g.get("owners", [])
            value = [{"id": uid, "userPrincipalName": f"{uid.replace('mock-user-', '')}@ttu.ac.ke"}
                     for uid in ids]
            return {"value": value, "count": len(value),
                    "timestamp": datetime.utcnow().isoformat()}
    raise HTTPException(status_code=404, detail="Group not found")


@app.post("/api/groups/{group_id}/join")
async def join_group(group_id: str, req: GroupActionRequest):
    """Student-only: join a group as a member."""
    student_email = require_role(str(req.email), "student")
    user_id = await resolve_user_id_by_email(student_email)

    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}/members/$ref", "POST", {
            "@odata.id": f"https://graph.microsoft.com/v1.0/users/{user_id}"
        })
        log.info(f"AUDIT group_join id={group_id} by={student_email}")
        return {"message": "Joined group successfully"}

    for g in mock_groups:
        if g["id"] == group_id:
            g.setdefault("members", []).append(user_id)
            log.info(f"AUDIT group_join (mock) id={group_id} by={student_email}")
            return {"message": "Joined group successfully"}
    raise HTTPException(status_code=404, detail="Group not found")


@app.post("/api/groups/{group_id}/leave")
async def leave_group(group_id: str, req: GroupActionRequest):
    """Student-only: leave a group."""
    student_email = require_role(str(req.email), "student")
    user_id = await resolve_user_id_by_email(student_email)

    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}/members/{user_id}/$ref", "DELETE")
        log.info(f"AUDIT group_leave id={group_id} by={student_email}")
        return {"message": "Left group successfully"}

    for g in mock_groups:
        if g["id"] == group_id:
            members = g.get("members", [])
            if user_id in members:
                members.remove(user_id)
            log.info(f"AUDIT group_leave (mock) id={group_id} by={student_email}")
            return {"message": "Left group successfully"}
    raise HTTPException(status_code=404, detail="Group not found")


@app.post("/api/groups/{group_id}/members")
async def add_group_member(group_id: str, body: dict = Body(...)):
    """Staff-owner-only: add a member by userId or email."""
    email = body.get("email")
    user_id = body.get("userId")
    if not email and not user_id:
        raise HTTPException(status_code=400, detail="email or userId is required")

    if email:
        staff_email = require_role(str(email), "staff")
        requester_id = await resolve_user_id_by_email(staff_email)
    else:
        requester_id = None

    if requester_id and not await _is_group_owner(group_id, requester_id):
        raise HTTPException(status_code=403, detail="Only a group owner may add members")

    target_id = user_id or await resolve_user_id_by_email(body["memberEmail"])

    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}/members/$ref", "POST", {
            "@odata.id": f"https://graph.microsoft.com/v1.0/users/{target_id}"
        })
    return {"message": "Member added to group successfully"}


@app.delete("/api/groups/{group_id}/members/{member_id}")
async def remove_group_member(group_id: str, member_id: str, email: Optional[str] = None):
    """Staff-owner-only: remove a member."""
    if not email:
        raise HTTPException(status_code=400, detail="email is required to authorize this action")
    staff_email = require_role(str(email), "staff")
    requester_id = await resolve_user_id_by_email(staff_email)
    if not await _is_group_owner(group_id, requester_id):
        raise HTTPException(status_code=403, detail="Only a group owner may remove members")

    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}/members/{member_id}/$ref", "DELETE")
    return {"message": "Member removed successfully"}


# ========== LICENSES ==========
@app.get("/api/licenses")
async def list_licenses():
    if HAS_AZURE_CREDENTIALS:
        licenses = await call_graph_api("/subscribedSkus")
        value = [{
            "id": l["skuPartNumber"],
            "skuId": l["skuId"],
            "skuPartNumber": l["skuPartNumber"],
            "appliesTo": l.get("appliesTo"),
            "productName": l["skuPartNumber"],
            "totalUnits": l["prepaidUnits"]["enabled"],
            "consumedUnits": l["consumedUnits"],
            "availableUnits": l["prepaidUnits"]["enabled"] - l["consumedUnits"],
            "status": l.get("capabilityStatus"),
        } for l in licenses.get("value", [])]
        return {"value": value, "count": len(value),
                "timestamp": datetime.utcnow().isoformat()}
    return {"value": mock_licenses, "count": len(mock_licenses),
            "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/licenses/{license_id}")
async def get_license(license_id: str):
    if HAS_AZURE_CREDENTIALS:
        licenses = await call_graph_api("/subscribedSkus")
        for l in licenses.get("value", []):
            if l["skuPartNumber"] == license_id:
                return l
        raise HTTPException(status_code=404, detail="License not found")
    for l in mock_licenses:
        if l["id"] == license_id:
            return l
    raise HTTPException(status_code=404, detail="License not found")


@app.post("/api/licenses/{license_id}/assign")
async def assign_license(license_id: str, body: dict = Body(...)):
    user_id = body.get("userId")
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/users/{user_id}/assignLicense", "POST", {
            "addLicenses": [{"skuId": license_id}],
            "removeLicenses": [],
        })
    return {"message": "License assigned successfully"}


@app.post("/api/licenses/{license_id}/remove")
async def remove_license(license_id: str, body: dict = Body(...)):
    user_id = body.get("userId")
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/users/{user_id}/assignLicense", "POST", {
            "addLicenses": [],
            "removeLicenses": [license_id],
        })
    return {"message": "License removed successfully"}


@app.get("/api/users/{user_id}/licenses")
async def get_user_licenses(user_id: str):
    if HAS_AZURE_CREDENTIALS:
        licenses = await call_graph_api(f"/users/{user_id}/licenseDetails")
        value = [{
            "skuId": l["skuId"],
            "skuPartNumber": l["skuPartNumber"],
            "productName": l["skuPartNumber"],
            "assigned": True,
        } for l in licenses.get("value", [])]
        return {"value": value, "count": len(value),
                "timestamp": datetime.utcnow().isoformat()}
    return {"value": [{**l, "assigned": True} for l in mock_licenses[:2]],
            "count": 2, "timestamp": datetime.utcnow().isoformat()}


# ========== SELF-REGISTRATION ==========
@app.post("/api/self-register", status_code=201)
async def self_register(req: SelfRegisterRequest):
    # Validate license type
    if req.licenseType not in LICENSE_SKU_MAP:
        raise HTTPException(status_code=400, detail={
            "error": "Invalid license type",
            "validTypes": list(LICENSE_SKU_MAP.keys()),
        })
    # Validate email domain
    valid_domains = ("@students.ttu.ac.ke", "@ttu.ac.ke")
    if not req.email.endswith(valid_domains):
        raise HTTPException(status_code=400, detail={
            "error": "Invalid email domain",
            "message": "Email must be from @students.ttu.ac.ke or @ttu.ac.ke",
        })

    if not HAS_AZURE_CREDENTIALS:
        mock_id = f"mock-{int(datetime.utcnow().timestamp() * 1000)}"
        mock_users.append({
            "id": mock_id, "displayName": req.displayName,
            "userPrincipalName": req.email, "mail": req.email,
            "givenName": req.firstName, "surname": req.lastName,
            "accountEnabled": True,
            "createdDateTime": datetime.utcnow().isoformat(),
        })
        return {
            "success": True, "userId": mock_id, "email": req.email,
            "displayName": req.displayName,
            "licenseAssigned": LICENSE_SKU_MAP[req.licenseType]["displayName"],
            "message": "Account created successfully (mock mode)",
            "timestamp": datetime.utcnow().isoformat(),
        }

    # Real Azure AD creation
    user_payload = {
        "accountEnabled": True,
        "displayName": req.displayName,
        "mailNickname": req.email.split("@")[0],
        "userPrincipalName": req.email,
        "givenName": req.firstName,
        "surname": req.lastName,
        "usageLocation": "KE",
        "passwordProfile": {
            "forceChangePasswordNextSignIn": True,
            "password": req.password,
        },
    }
    try:
        new_user = await call_graph_api("/users", "POST", user_payload)
    except HTTPException as e:
        if "already exists" in str(e.detail):
            raise HTTPException(status_code=409, detail={
                "success": False, "error": "User already exists",
            })
        raise

    # # Assign license
    # license_assigned = None
    # license_warning = None
    # try:
    #     licenses = await call_graph_api("/subscribedSkus")
    #     cfg = LICENSE_SKU_MAP[req.licenseType]
    #     match = next((l for l in licenses.get("value", []) if (
    #         cfg["skuId"] in l["skuPartNumber"] or
    #         req.licenseType.upper() in l["skuPartNumber"]
    #     )), None)
    #     if match and match["prepaidUnits"]["enabled"] > match["consumedUnits"]:
    #         await call_graph_api(f"/users/{new_user['id']}/assignLicense", "POST", {
    #             "addLicenses": [{"skuId": match["skuId"]}],
    #             "removeLicenses": [],
    #         })
    #         license_assigned = match["skuPartNumber"]
    #     else:
    #         license_warning = "No available licenses or license not found"
    # except Exception as e:
    #     license_warning = f"License assignment failed: {e}"

    # ---- Assign license(s) ----
    license_assigned = None          # e.g. "STANDARDWOFFPACK_FACULTY" (first successful)
    licenses_assigned = []           # list of all successfully assigned
    license_warning = None
    try:
        # 1. Fetch what the tenant actually has
        tenant = await call_graph_api("/subscribedSkus")
        available = {
            l["skuPartNumber"]: l
            for l in tenant.get("value", [])
        }
        log.info(f"Available SKUs in tenant: {list(available.keys())}")

        cfg = LICENSE_SKU_MAP[req.licenseType]

        # 2. Resolve each configured SKU against the tenant
        to_assign = []          # [{skuId, label, required}]
        skipped = []            # [(label, reason)]
        for entry in cfg["skus"]:
            t = available.get(entry["skuPartNumber"])
            if not t:
                msg = f"{entry['label']}: not in tenant"
                if entry["required"]:
                    raise RuntimeError(msg)
                skipped.append(msg)
                continue

            remaining = t["prepaidUnits"]["enabled"] - t["consumedUnits"]
            if remaining <= 0:
                msg = f"{entry['label']}: no seats left ({t['consumedUnits']}/{t['prepaidUnits']['enabled']})"
                if entry["required"]:
                    raise RuntimeError(msg)
                skipped.append(msg)
                continue

            to_assign.append({
                "skuId": t["skuId"],           # use tenant's GUID (safest)
                "label": entry["label"],
                "required": entry["required"],
            })

        if not to_assign:
            raise RuntimeError(f"No assignable SKUs for {req.licenseType}: {skipped}")

        # 3. Send one bulk assignLicense request
        log.info(f"Assigning {len(to_assign)} license(s) to {new_user['id']}: "
                 f"{[a['label'] for a in to_assign]}")

        await call_graph_api(
            f"/users/{new_user['id']}/assignLicense",
            "POST",
            {
                "addLicenses": [{"skuId": a["skuId"]} for a in to_assign],
                "removeLicenses": [],
            },
        )

        licenses_assigned = [a["label"] for a in to_assign]
        license_assigned = to_assign[0]["label"]   # primary label for the response
        log.info(f"✅ Licenses assigned: {licenses_assigned}")
        if skipped:
            license_warning = "Partial: skipped " + "; ".join(skipped)
            log.warning(license_warning)

    except Exception as e:
        license_warning = f"License assignment failed: {e}"
        log.error(license_warning, exc_info=True)

    # Send welcome email via email_service (async)
    email_sent = False
    email_error = None
    if email_service is None:
        email_error = "Email service not configured"
    else:
        try:
            result = await email_service.send_account_creation_email({
                    "email": req.email,
                    "firstName": req.firstName,
                    "lastName": req.lastName,
                    "displayName": req.displayName,
                    "licenseType": req.licenseType,
                    "password": req.password,
                   })
            email_sent = result.get("success", False)
            email_error = result.get("error")

            if email_sent:
                        try:
                            await email_service.send_welcome_resources_email(req.email, req.firstName)
                        except Exception as e:
                            log.warning(f"Welcome resources email failed: {e}")
        except Exception as e:
                log.error(f"Email sending error: {e}")
                email_error = str(e)

        # return {
        #         "success": True,
        #         "userId": new_user["id"],
        #         "email": new_user["userPrincipalName"],
        #         "displayName": new_user["displayName"],
        #         "licenseAssigned": license_assigned,
        #         "licenseWarning": license_warning,
        #         "emailSent": email_sent,
        #         "emailError": email_error,
        #         "message": "Account created" + (" and license assigned" if license_assigned else "") + " successfully",
        #         "timestamp": datetime.utcnow().isoformat(),
        #     }
        return {
            "success": True,
            "userId": new_user["id"],
            "email": new_user["userPrincipalName"],
            "displayName": new_user["displayName"],
            "licenseAssigned": license_assigned,             # primary (backwards compat)
            "licensesAssigned": licenses_assigned,           # full list
            "licenseWarning": license_warning,
            "emailSent": email_sent,
            "emailError": email_error,
            "message": (
                f"Account created with {len(licenses_assigned)} license(s)"
                if licenses_assigned else "Account created"
            ),
            "timestamp": datetime.utcnow().isoformat(),
        }        



# ========== START SERVER ==========
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "5000"))
    log.info(f"🚀 Azure Admin API Server starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)