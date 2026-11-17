"""User endpoints + email/role verification."""
import logging
from datetime import datetime

from api import config
from api.exceptions import ApiError
from api.graph import call_graph_api
from api.helpers import format_user, get_role
from api.mock_data import mock_users
from api.schemas import BulkRequest, VerifyEmailRequest
from api.views.common import api_view, json_response, parse_json_body, parse_model

log = logging.getLogger("azure-admin")


@api_view("POST")
async def verify_email(request):
    """
    Simplified TTU role verification.

    NOTE: This only checks the DOMAIN of the supplied email — it does NOT prove
    the caller owns the address. In production, replace this with an OAuth 2.0 /
    OpenID Connect flow and derive the role from verified ID token claims.
    """
    req = parse_model(VerifyEmailRequest, request)
    email = req.email.strip().lower()
    role = get_role(email)
    if role is None:
        raise ApiError(400, {"error": "Invalid TTU email domain"})

    # Best-effort: confirm the account exists in the directory.
    exists = None
    if config.HAS_AZURE_CREDENTIALS:
        try:
            await call_graph_api(f"/users/{email}?$select=id,displayName,userPrincipalName")
            exists = True
        except ApiError as e:
            if e.status_code == 404:
                exists = False
            else:
                exists = None

    log.info(f"verify-email: {email} -> role={role} (exists={exists})")
    return json_response({"email": email, "role": role, "existsInDirectory": exists})


@api_view("GET", "POST")
async def users_collection(request):
    if request.method == "POST":
        return await _create_user(request)

    if config.HAS_AZURE_CREDENTIALS:
        query = (
            "/users?$select=id,displayName,userPrincipalName,mail,jobTitle,"
            "department,accountEnabled,createdDateTime,mobilePhone,"
            "officeLocation,usageLocation,userType&$top=999"
        )
        users = await call_graph_api(query)
        value = [format_user(u) for u in users.get("value", [])]
        return json_response({"value": value, "count": len(value), "total": len(value),
                              "timestamp": datetime.utcnow().isoformat()})
    return json_response({"value": mock_users, "count": len(mock_users),
                          "total": len(mock_users), "timestamp": datetime.utcnow().isoformat()})


async def _create_user(request):
    user_data = parse_json_body(request)
    if config.HAS_AZURE_CREDENTIALS:
        new_user = await call_graph_api("/users", "POST", user_data)
        return json_response(format_user(new_user), status=201)
    new_user = {
        "id": f"mock-{int(datetime.utcnow().timestamp() * 1000)}",
        **user_data,
        "createdDateTime": datetime.utcnow().isoformat(),
        "accountEnabled": True,
    }
    mock_users.append(new_user)
    return json_response(new_user, status=201)


@api_view("GET", "PUT", "DELETE")
async def user_detail(request, user_id: str):
    if request.method == "PUT":
        return await _update_user(request, user_id)
    if request.method == "DELETE":
        return await _delete_user(request, user_id)

    if config.HAS_AZURE_CREDENTIALS:
        user = await call_graph_api(f"/users/{user_id}")
        return json_response(format_user(user))
    for u in mock_users:
        if u["id"] == user_id:
            return json_response(u)
    raise ApiError(404, "User not found")


async def _update_user(request, user_id: str):
    updates = parse_json_body(request)
    allowed = {
        "displayName", "givenName", "surname", "mailNickname", "jobTitle",
        "department", "mobilePhone", "officeLocation", "usageLocation",
        "preferredLanguage", "accountEnabled",
    }
    sanitized = {k: v for k, v in updates.items() if k in allowed}
    log.info(f"PUT /api/users/{user_id} - sanitized keys: {list(sanitized.keys())}")

    if config.HAS_AZURE_CREDENTIALS:
        updated = await call_graph_api(f"/users/{user_id}", "PATCH", sanitized)
        return json_response(format_user(updated) if updated else {"message": "updated"})
    for u in mock_users:
        if u["id"] == user_id:
            u.update(sanitized)
            return json_response(u)
    raise ApiError(404, "User not found")


async def _delete_user(request, user_id: str):
    if config.HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/users/{user_id}", "DELETE")
        return json_response({"message": "User deleted successfully"})
    for i, u in enumerate(mock_users):
        if u["id"] == user_id:
            mock_users.pop(i)
            return json_response({"message": "User deleted successfully"})
    raise ApiError(404, "User not found")


@api_view("POST")
async def bulk_users(request):
    req = parse_model(BulkRequest, request)
    results = []
    if config.HAS_AZURE_CREDENTIALS:
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
    return json_response({"operation": req.operation, "processed": len(results),
                          "results": results, "timestamp": datetime.utcnow().isoformat()})


@api_view("POST")
async def import_users(request):
    body = parse_json_body(request)
    users = body.get("users", [])
    if not isinstance(users, list):
        raise ApiError(400, "Expected array of users")
    results = []
    if config.HAS_AZURE_CREDENTIALS:
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
    return json_response({"total": len(users), "processed": len(results),
                          "results": results, "timestamp": datetime.utcnow().isoformat()})
