"""Bulk member addition from .xlsx/.xlsm/.csv uploads (staff-only)."""
import asyncio
import csv
import io
import logging
from datetime import datetime

from django.http import HttpResponse

from api import config
from api.caching import cache_invalidate
from api.exceptions import ApiError
from api.graph import bulk_semaphore, call_graph_api, graph_request_with_retry
from api.helpers import csv_safe, get_role, parse_emails_from_upload, require_role, resolve_user_id_by_email
from api.mock_data import mock_groups
from api.views.common import api_view, json_response
from api.views.groups import _is_group_owner

log = logging.getLogger("azure-admin")


async def _bulk_add_one(group_id: str, row: int, email: str, existing_ids: set[str]) -> dict:
    """Validate & add a single email. Returns a per-row result dict.

    Order: domain check → directory lookup → already-member check → Graph add.
    Never raises; failures become status values so the batch always completes.
    """
    normalized = email.strip().lower()

    # 1) Domain: staff or student institutional domains only.
    role = get_role(normalized)
    if role is None:
        return {"row": row, "email": email, "status": "invalid_domain"}

    # 2) Resolve to a directory user id.
    try:
        user_id = await resolve_user_id_by_email(normalized)
    except ApiError as e:
        if e.status_code == 404:
            return {"row": row, "email": normalized, "status": "not_found"}
        return {"row": row, "email": normalized, "status": "failed",
                "error": f"lookup {e.status_code}: {e.detail}"}

    # 3) Already a member?
    if user_id in existing_ids:
        return {"row": row, "email": normalized, "status": "already_member", "userId": user_id}

    # 4) Mock mode short-circuit.
    if not config.HAS_AZURE_CREDENTIALS:
        existing_ids.add(user_id)
        return {"row": row, "email": normalized, "status": "added",
                "userId": user_id, "role": role}

    # 5) Real Graph add (throttle-aware).
    try:
        async with bulk_semaphore():
            await graph_request_with_retry(
                f"/groups/{group_id}/members/$ref",
                "POST",
                {"@odata.id": f"https://graph.microsoft.com/v1.0/users/{user_id}"},
            )
        existing_ids.add(user_id)
        return {"row": row, "email": normalized, "status": "added",
                "userId": user_id, "role": role}
    except ApiError as e:
        # Graph returns 400/409 when the member already exists (race).
        if e.status_code in (400, 409) and "already exist" in str(e.detail).lower():
            return {"row": row, "email": normalized, "status": "already_member",
                    "userId": user_id}
        return {"row": row, "email": normalized, "status": "failed",
                "userId": user_id, "error": f"graph {e.status_code}: {e.detail}"}
    except Exception as e:
        return {"row": row, "email": normalized, "status": "failed", "error": str(e)}


async def _bulk_add_members_impl(group_id: str, filename: str, content: bytes, email: str) -> dict:
    """Core bulk-add flow; returns the report dict (shared by both endpoints)."""
    # ---- 1) Authorization (fail fast, before touching the file) ----
    staff_email = require_role(email, "staff")
    requester_id = await resolve_user_id_by_email(staff_email)
    if not await _is_group_owner(group_id, requester_id):
        raise ApiError(403, "Only a group owner may add members")

    # ---- 2) Guard the upload ----
    if len(content) == 0:
        raise ApiError(400, "Uploaded file is empty")
    if len(content) > config.BULK_MAX_BYTES:
        raise ApiError(413, {
            "error": "File too large", "maxBytes": config.BULK_MAX_BYTES,
            "receivedBytes": len(content),
        })

    # ---- 3) Parse ----
    parsed = parse_emails_from_upload(filename or "", content)
    if not parsed:
        raise ApiError(400, {
            "error": "No email rows found",
            "hint": f"Expected a '${config.BULK_EMAIL_COLUMN}' column.",
        })

    # ---- 4) Row cap: process first N, report the rest ----
    skipped_over_cap = 0
    if len(parsed) > config.BULK_MAX_ROWS:
        skipped_over_cap = len(parsed) - config.BULK_MAX_ROWS
        parsed = parsed[:config.BULK_MAX_ROWS]

    # ---- 5) De-duplicate (keep first occurrence) ----
    seen: dict[str, int] = {}
    unique_rows: list[tuple[int, str]] = []
    dup_rows: list[tuple[int, str]] = []
    for row_no, raw in parsed:
        key = raw.strip().lower()
        if key in seen:
            dup_rows.append((row_no, raw))
        else:
            seen[key] = row_no
            unique_rows.append((row_no, raw))

    # ---- 6) Snapshot current members (for idempotency) ----
    existing_ids: set[str] = set()
    if config.HAS_AZURE_CREDENTIALS:
        try:
            members = await call_graph_api(f"/groups/{group_id}/members?$select=id")
            existing_ids = {m.get("id") for m in members.get("value", [])}
        except ApiError:
            existing_ids = set()
    else:
        for g in mock_groups:
            if g["id"] == group_id:
                existing_ids = set(g.get("members", []))

    # ---- 7) Process with bounded parallelism ----
    tasks = [_bulk_add_one(group_id, row_no, raw, existing_ids)
             for row_no, raw in unique_rows]
    results = await asyncio.gather(*tasks) if tasks else []
    results = list(results)
    results.extend({"row": r, "email": e, "status": "duplicate"} for r, e in dup_rows)
    results.sort(key=lambda x: x["row"])

    # ---- 8) Invalidate caches that may hold stale membership ----
    cache_invalidate("members:", f"members:{group_id}", "groups:", "studentgroups:")

    # ---- 9) Summarize ----
    def _count(status: str) -> int:
        return sum(1 for r in results if r["status"] == status)

    summary = {
        "fileName": filename,
        "totalRows": len(parsed),
        "uniqueEmails": len(unique_rows),
        "added": _count("added"),
        "alreadyMember": _count("already_member"),
        "invalidDomain": _count("invalid_domain"),
        "notFound": _count("not_found"),
        "duplicatesInFile": len(dup_rows),
        "failed": _count("failed"),
        "skippedOverCap": skipped_over_cap,
    }

    # ---- 10) Audit ----
    log.info(
        f"AUDIT bulk_member_add group={group_id} by={staff_email} "
        f"file='{filename}' added={summary['added']} "
        f"already={summary['alreadyMember']} failed={summary['failed']} "
        f"invalid={summary['invalidDomain']} notfound={summary['notFound']}"
    )
    return {
        "group": {"id": group_id},
        "requestedBy": staff_email,
        "summary": summary,
        "results": results,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _read_upload(request) -> tuple[str, bytes, str]:
    """Extract (filename, content, staff email) from the multipart request."""
    upload = request.FILES.get("file")
    if upload is None:
        raise ApiError(400, "file is required (multipart field 'file')")
    email = request.POST.get("email")
    if not email:
        raise ApiError(400, "email is required (multipart field 'email')")
    return upload.name, upload.read(), email


@api_view("POST")
async def bulk_add_members(request, group_id: str):
    """Staff-owner-only: add members to a group in bulk from .xlsx/.xlsm/.csv.

    - Only staff may call this; only a group *owner* may modify the group.
    - Each row is validated (domain → existence → not-already-member) and added
      via Graph with bounded concurrency + throttle-aware retries.
    - Returns a per-row report plus a summary. Idempotent: safe to re-run.
    """
    filename, content, email = _read_upload(request)
    report = await _bulk_add_members_impl(group_id, filename, content, email)
    return json_response(report)


@api_view("POST")
async def bulk_add_members_report(request, group_id: str):
    """Same as the bulk endpoint but returns the report as a CSV download."""
    filename, content, email = _read_upload(request)
    report = await _bulk_add_members_impl(group_id, filename, content, email)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Row", "Email", "Status", "UserId", "Detail"])
    for r in report["results"]:
        writer.writerow([
            csv_safe(r.get("row")),
            csv_safe(r.get("email")),
            csv_safe(r.get("status")),
            csv_safe(r.get("userId", "")),
            csv_safe(r.get("error", "")),
        ])
    fname = f"member-upload-report-{group_id[:8]}.csv"
    resp = HttpResponse(buf.getvalue(), content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{fname}"'
    return resp


@api_view("GET")
async def bulk_members_template(request):
    """Download a ready-to-fill .xlsx template for the bulk upload."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Members"
    ws.append(["email", "name (ignored)"])
    ws.append(["student1@students.ttu.ac.ke", "Example Student"])
    ws.append(["staff1@ttu.ac.ke", "Example Staff"])
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="bulk-members-template.xlsx"'
    return resp
