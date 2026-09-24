import os
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
    allow_origins=["http://localhost:3000", "http://127.0.0.1:5500"],
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
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(method, url, headers=headers, json=data)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()
    except httpx.HTTPStatusError as e:
        log.error(f"Graph API error for {endpoint}: {e}")
        log.error(f"Graph body: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)


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

LICENSE_SKU_MAP = {
    "Student": {"skuId": "STANDARDWOFFPACK_STUDENT", "displayName": "Microsoft 365 A1 for Students"},
    "Faculty": {"skuId": "STANDARDWOFFPACK_FACULTY", "displayName": "Microsoft 365 A3 for Faculty"},
}

# ========== REQUEST LOGGING MIDDLEWARE ==========
@app.middleware("http")
async def log_requests(request: Request, call_next):
    log.info(f"➡️ {request.method} {request.url.path}")
    response = await call_next(request)
    return response


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


# ========== GROUPS ==========
@app.get("/api/groups")
async def list_groups():
    if HAS_AZURE_CREDENTIALS:
        groups = await call_graph_api(
            "/groups?$select=id,displayName,description,mailEnabled,"
            "securityEnabled,createdDateTime,mail,groupTypes&$top=999"
        )
        value = [format_group(g) for g in groups.get("value", [])]
        return {"value": value, "count": len(value), "total": len(value),
                "timestamp": datetime.utcnow().isoformat()}
    return {"value": mock_groups, "count": len(mock_groups),
            "total": len(mock_groups), "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/groups/{group_id}")
async def get_group(group_id: str):
    if HAS_AZURE_CREDENTIALS:
        return format_group(await call_graph_api(f"/groups/{group_id}"))
    for g in mock_groups:
        if g["id"] == group_id:
            return g
    raise HTTPException(status_code=404, detail="Group not found")


@app.post("/api/groups", status_code=201)
async def create_group(group_data: dict = Body(...)):
    if HAS_AZURE_CREDENTIALS:
        return format_group(await call_graph_api("/groups", "POST", group_data))
    new_group = {"id": f"mock-group-{int(datetime.utcnow().timestamp() * 1000)}",
                 **group_data, "createdDateTime": datetime.utcnow().isoformat(),
                 "securityEnabled": True}
    mock_groups.append(new_group)
    return new_group


@app.put("/api/groups/{group_id}")
async def update_group(group_id: str, updates: dict = Body(...)):
    if HAS_AZURE_CREDENTIALS:
        return format_group(await call_graph_api(f"/groups/{group_id}", "PATCH", updates))
    for g in mock_groups:
        if g["id"] == group_id:
            g.update(updates)
            return g
    raise HTTPException(status_code=404, detail="Group not found")


@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: str):
    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}", "DELETE")
        return {"message": "Group deleted successfully"}
    for i, g in enumerate(mock_groups):
        if g["id"] == group_id:
            mock_groups.pop(i)
            return {"message": "Group deleted successfully"}
    raise HTTPException(status_code=404, detail="Group not found")


@app.post("/api/groups/{group_id}/members")
async def add_group_member(group_id: str, body: dict = Body(...)):
    user_id = body.get("userId")
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")
    if HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}/members/$ref", "POST", {
            "@odata.id": f"https://graph.microsoft.com/v1.0/users/{user_id}"
        })
    return {"message": "User added to group successfully"}


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

    # Assign license
    license_assigned = None
    license_warning = None
    try:
        licenses = await call_graph_api("/subscribedSkus")
        cfg = LICENSE_SKU_MAP[req.licenseType]
        match = next((l for l in licenses.get("value", []) if (
            cfg["skuId"] in l["skuPartNumber"] or
            req.licenseType.upper() in l["skuPartNumber"]
        )), None)
        if match and match["prepaidUnits"]["enabled"] > match["consumedUnits"]:
            await call_graph_api(f"/users/{new_user['id']}/assignLicense", "POST", {
                "addLicenses": [{"skuId": match["skuId"]}],
                "removeLicenses": [],
            })
            license_assigned = match["skuPartNumber"]
        else:
            license_warning = "No available licenses or license not found"
    except Exception as e:
        license_warning = f"License assignment failed: {e}"

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

        return {
                "success": True,
                "userId": new_user["id"],
                "email": new_user["userPrincipalName"],
                "displayName": new_user["displayName"],
                "licenseAssigned": license_assigned,
                "licenseWarning": license_warning,
                "emailSent": email_sent,
                "emailError": email_error,
                "message": "Account created" + (" and license assigned" if license_assigned else "") + " successfully",
                "timestamp": datetime.utcnow().isoformat(),
            }



# ========== START SERVER ==========
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "5000"))
    log.info(f"🚀 Azure Admin API Server starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)