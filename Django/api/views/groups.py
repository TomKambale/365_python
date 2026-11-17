"""Group endpoints (staff-led management + student read/leave)."""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from api import config
from api.caching import cache_invalidate
from api.exceptions import ApiError
from api.graph import call_graph_api
from api.helpers import format_group, get_role, require_role, resolve_user_id_by_email
from api.mock_data import mock_groups
from api.schemas import GroupActionRequest, StaffGroupRequest
from api.views.common import api_view, json_response, parse_json_body, parse_model

log = logging.getLogger("azure-admin")

UNIFIED_FILTER = "/groups?$filter=groupTypes/any(c:c eq 'Unified')&$top=999&$select=id,displayName,description,mail,visibility,createdDateTime,mailEnabled,securityEnabled,groupTypes"


async def _fetch_unified_groups() -> list[dict]:
    """Fetch all Microsoft 365 (Unified) groups, formatted."""
    if config.HAS_AZURE_CREDENTIALS:
        try:
            groups = await call_graph_api(UNIFIED_FILTER)
        except ApiError as e:
            # Only skip the fallback for OUR "cannot reach Graph" 502 — that
            # failure hits every endpoint, so retrying another URL is pointless.
            # Any other error (e.g. complex $filter rejected, or a transient
            # 5xx from Graph) MUST still fall back to the plain /groups list so
            # students keep seeing groups.
            if e.status_code == 502:
                raise
            log.warning(f"Unified $filter failed ({e.detail}); falling back to /groups")
            groups = await call_graph_api("/groups?$top=999&$select=id,displayName,description,mail,visibility,createdDateTime,mailEnabled,securityEnabled,groupTypes")
        unified_raw = [x for x in groups.get("value", []) if "Unified" in (x.get("groupTypes") or [])]
        value = [format_group(g) for g in unified_raw]
        for g, raw in zip(value, unified_raw):
            g["visibility"] = raw.get("visibility")
        return value
    # Mock mode: only return Unified groups
    return [g for g in mock_groups if "Unified" in (g.get("groupTypes") or [])]


async def _is_group_owner(group_id: str, user_id: str) -> bool:
    """Return True if user_id is an owner of group_id."""
    if not config.HAS_AZURE_CREDENTIALS:
        for g in mock_groups:
            if g["id"] == group_id:
                return user_id in g.get("owners", [])
        return False
    try:
        owners = await call_graph_api(f"/groups/{group_id}/owners?$select=id")
    except ApiError as e:
        if e.status_code == 404:
            raise ApiError(404, "Group not found")
        raise
    return any(o.get("id") == user_id for o in owners.get("value", []))


async def _groups_for_student(all_groups: list[dict], student_email: str) -> list[dict]:
    """Filter the group list to those the student belongs to (read-only view)."""
    if not config.HAS_AZURE_CREDENTIALS:
        uid = f"mock-user-{student_email.split('@')[0]}"
        return [g for g in all_groups if uid in g.get("members", [])]

    try:
        user_id = await resolve_user_id_by_email(student_email)
    except ApiError:
        return []

    async def member_of(g: dict) -> bool:
        try:
            m = await call_graph_api(f"/groups/{g['id']}/members?$select=id")
            return any(x.get("id") == user_id for x in m.get("value", []))
        except ApiError:
            return False

    flags = await asyncio.gather(*[member_of(g) for g in all_groups])
    return [g for g, is_m in zip(all_groups, flags) if is_m]


@api_view("GET", "POST")
async def groups_collection(request):
    if request.method == "POST":
        return await _create_group(request)

    # List Microsoft 365 (Unified) groups.
    # - With no ``email`` (or a staff email): returns ALL groups (admin view).
    # - With a *student* email: returns ONLY groups the student is a member of.
    email: Optional[str] = request.GET.get("email")
    value = await _fetch_unified_groups()

    if email:
        role = get_role(email)
        if role == "student":
            value = await _groups_for_student(value, str(email).strip().lower())

    return json_response({"value": value, "count": len(value), "total": len(value),
                          "timestamp": datetime.utcnow().isoformat()})


async def _create_group(request):
    """Staff-only: create a Unified group and set the creator as owner."""
    req = parse_model(StaffGroupRequest, request)
    staff_email = require_role(str(req.email), "staff")

    display_name = (req.displayName or "").strip()
    if not display_name:
        raise ApiError(400, "displayName is required")

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

    if config.HAS_AZURE_CREDENTIALS:
        created = await call_graph_api("/groups", "POST", payload)
        group_id = created["id"]

        # Add the creator as an owner
        try:
            user_id = await resolve_user_id_by_email(staff_email)
            await call_graph_api(f"/groups/{group_id}/owners/$ref", "POST", {
                "@odata.id": f"https://graph.microsoft.com/v1.0/users/{user_id}"
            })
        except ApiError as e:
            log.error(f"Failed to set owner on group {group_id}: {e.detail}")

        log.info(f"AUDIT group_created id={group_id} name='{display_name}' by={staff_email}")
        return json_response(format_group(created), status=201)

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
    return json_response(format_group(new_group), status=201)


@api_view("GET")
async def list_student_groups(request, email: str):
    """Student-only: the groups this student belongs to (read-only 'My Groups')."""
    student_email = require_role(str(email), "student")
    all_groups = await _fetch_unified_groups()
    value = await _groups_for_student(all_groups, student_email)
    for g in value:
        g["isMember"] = True
    return json_response({"value": value, "count": len(value),
                          "timestamp": datetime.utcnow().isoformat()})


@api_view("GET")
async def list_owned_groups(request):
    """List Unified groups owned by the given staff email."""
    email = request.GET.get("email")
    require_role(str(email or ""), "staff")
    user_id = await resolve_user_id_by_email(str(email))
    all_groups = await _fetch_unified_groups()

    owned = []
    if config.HAS_AZURE_CREDENTIALS:
        for g in all_groups:
            try:
                if await _is_group_owner(g["id"], user_id):
                    owned.append(g)
            except ApiError:
                continue
    else:
        for g in all_groups:
            if user_id in g.get("owners", []):
                owned.append(g)
    return json_response({"value": owned, "count": len(owned),
                          "timestamp": datetime.utcnow().isoformat()})


@api_view("GET", "PUT", "DELETE")
async def group_detail(request, group_id: str):
    if request.method == "PUT":
        return await _update_group(request, group_id)
    if request.method == "DELETE":
        return await _delete_group(request, group_id)

    if config.HAS_AZURE_CREDENTIALS:
        return json_response(format_group(await call_graph_api(
            f"/groups/{group_id}?$select=id,displayName,description,mail,visibility,createdDateTime,mailEnabled,securityEnabled,groupTypes"
        )))
    for g in mock_groups:
        if g["id"] == group_id:
            return json_response(g)
    raise ApiError(404, "Group not found")


async def _update_group(request, group_id: str):
    """Staff-owner-only: update group metadata."""
    updates = parse_json_body(request)
    email = updates.pop("email", None)
    if not email:
        raise ApiError(400, "email is required to authorize this action")
    staff_email = require_role(str(email), "staff")
    user_id = await resolve_user_id_by_email(staff_email)
    if not await _is_group_owner(group_id, user_id):
        raise ApiError(403, "Only a group owner may update this group")

    allowed = {"displayName", "description", "visibility"}
    sanitized = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not sanitized:
        raise ApiError(400, "No valid fields to update")

    if config.HAS_AZURE_CREDENTIALS:
        updated = await call_graph_api(f"/groups/{group_id}", "PATCH", sanitized)
        log.info(f"AUDIT group_updated id={group_id} fields={list(sanitized)} by={staff_email}")
        return json_response(
            format_group(updated) if updated else {"id": group_id, "message": "updated", **sanitized}
        )
    for g in mock_groups:
        if g["id"] == group_id:
            g.update(sanitized)
            log.info(f"AUDIT group_updated (mock) id={group_id} by={staff_email}")
            return json_response(g)
    raise ApiError(404, "Group not found")


async def _delete_group(request, group_id: str):
    """Staff-owner-only: delete a group (email via query param or JSON body)."""
    requester = request.GET.get("email") or parse_json_body(request).get("email")
    if not requester:
        raise ApiError(400, "email is required to authorize this action")
    staff_email = require_role(str(requester), "staff")
    user_id = await resolve_user_id_by_email(staff_email)

    if not await _is_group_owner(group_id, user_id):
        raise ApiError(403, "Only a group owner may delete this group")

    if config.HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}", "DELETE")
        log.info(f"AUDIT group_deleted id={group_id} by={staff_email}")
        return json_response({"message": "Group deleted successfully"})
    for i, g in enumerate(mock_groups):
        if g["id"] == group_id:
            mock_groups.pop(i)
            log.info(f"AUDIT group_deleted (mock) id={group_id} by={staff_email}")
            return json_response({"message": "Group deleted successfully"})
    raise ApiError(404, "Group not found")


@api_view("GET", "POST")
async def group_members(request, group_id: str):
    if request.method == "POST":
        return await _add_group_member(request, group_id)

    # List the members of a group.
    if config.HAS_AZURE_CREDENTIALS:
        members = await call_graph_api(
            f"/groups/{group_id}/members?$select=id,displayName,userPrincipalName,mail"
        )
        value = [{
            "id": m.get("id"),
            "displayName": m.get("displayName"),
            "userPrincipalName": m.get("userPrincipalName"),
            "mail": m.get("mail"),
        } for m in members.get("value", [])]
        return json_response({"value": value, "count": len(value),
                              "timestamp": datetime.utcnow().isoformat()})
    for g in mock_groups:
        if g["id"] == group_id:
            ids = g.get("members", [])
            value = [{"id": uid, "userPrincipalName": f"{uid.replace('mock-user-', '')}@ttu.ac.ke"}
                     for uid in ids]
            return json_response({"value": value, "count": len(value),
                                  "timestamp": datetime.utcnow().isoformat()})
    raise ApiError(404, "Group not found")


@api_view("GET")
async def group_owners(request, group_id: str):
    """List the owners of a group."""
    if config.HAS_AZURE_CREDENTIALS:
        owners = await call_graph_api(
            f"/groups/{group_id}/owners?$select=id,displayName,userPrincipalName,mail"
        )
        value = [{
            "id": o.get("id"),
            "displayName": o.get("displayName"),
            "userPrincipalName": o.get("userPrincipalName"),
            "mail": o.get("mail"),
        } for o in owners.get("value", [])]
        return json_response({"value": value, "count": len(value),
                              "timestamp": datetime.utcnow().isoformat()})
    for g in mock_groups:
        if g["id"] == group_id:
            ids = g.get("owners", [])
            value = [{"id": uid, "userPrincipalName": f"{uid.replace('mock-user-', '')}@ttu.ac.ke"}
                     for uid in ids]
            return json_response({"value": value, "count": len(value),
                                  "timestamp": datetime.utcnow().isoformat()})
    raise ApiError(404, "Group not found")


@api_view("POST")
async def join_group(request, group_id: str):
    """DISABLED: students can no longer self-join groups.

    Membership is now a staff-driven, deliberate action (see the bulk upload
    endpoint). We keep this route so the frontend receives a clear, actionable
    message instead of a 404.
    """
    req = parse_model(GroupActionRequest, request)
    require_role(str(req.email), "student")  # still validate the caller's domain
    raise ApiError(403, {
        "error": "Self-join is disabled",
        "message": "Group membership is managed by staff. Ask a staff member to add you to this group.",
    })


@api_view("POST")
async def leave_group(request, group_id: str):
    """Student-only: leave a group (leaving is still permitted; joining is not)."""
    req = parse_model(GroupActionRequest, request)
    student_email = require_role(str(req.email), "student")
    user_id = await resolve_user_id_by_email(student_email)

    if config.HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}/members/{user_id}/$ref", "DELETE")
        cache_invalidate("members:", f"members:{group_id}", "groups:", "studentgroups:")
        log.info(f"AUDIT group_leave id={group_id} by={student_email}")
        return json_response({"message": "Left group successfully"})

    for g in mock_groups:
        if g["id"] == group_id:
            members = g.get("members", [])
            if user_id in members:
                members.remove(user_id)
            log.info(f"AUDIT group_leave (mock) id={group_id} by={student_email}")
            return json_response({"message": "Left group successfully"})
    raise ApiError(404, "Group not found")


async def _add_group_member(request, group_id: str):
    """Staff-owner-only: add a single member by email or userId.

    Prefer the bulk endpoint for lists; this remains for one-off adds. Enforces
    institutional domains (staff or student) and idempotency.
    """
    body = parse_json_body(request)
    email = body.get("email")
    member_email = body.get("memberEmail")
    target_user_id = body.get("userId")

    if not email:
        raise ApiError(400, "email (staff requester) is required")
    if not member_email and not target_user_id:
        raise ApiError(400, "memberEmail or userId is required")

    staff_email = require_role(str(email), "staff")
    requester_id = await resolve_user_id_by_email(staff_email)
    if not await _is_group_owner(group_id, requester_id):
        raise ApiError(403, "Only a group owner may add members")

    if target_user_id:
        target_id = str(target_user_id)
    else:
        normalized = str(member_email).strip().lower()
        if get_role(normalized) is None:
            raise ApiError(400, {
                "error": "Invalid member domain",
                "message": "Member must be @students.ttu.ac.ke or @ttu.ac.ke",
            })
        target_id = await resolve_user_id_by_email(normalized)

    if config.HAS_AZURE_CREDENTIALS:
        try:
            await call_graph_api(f"/groups/{group_id}/members/$ref", "POST", {
                "@odata.id": f"https://graph.microsoft.com/v1.0/users/{target_id}"
            })
        except ApiError as e:
            if e.status_code in (400, 409) and "already exist" in str(e.detail).lower():
                return json_response({"message": "Member already in group", "status": "already_member"})
            raise
        cache_invalidate("members:", f"members:{group_id}", "groups:", "studentgroups:")

    log.info(f"AUDIT group_member_add id={group_id} target={target_id} by={staff_email}")
    return json_response({"message": "Member added to group successfully"})


@api_view("DELETE")
async def remove_group_member(request, group_id: str, member_id: str):
    """Staff-owner-only: remove a member."""
    email = request.GET.get("email")
    if not email:
        raise ApiError(400, "email is required to authorize this action")
    staff_email = require_role(str(email), "staff")
    requester_id = await resolve_user_id_by_email(staff_email)
    if not await _is_group_owner(group_id, requester_id):
        raise ApiError(403, "Only a group owner may remove members")

    if config.HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/groups/{group_id}/members/{member_id}/$ref", "DELETE")
    return json_response({"message": "Member removed successfully"})
