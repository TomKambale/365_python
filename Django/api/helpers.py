"""Formatting, TTU role, and upload-parsing helpers (ported from main.py)."""
import csv
import io
import re
from pathlib import Path
from typing import Any, Optional

from api import config
from api.exceptions import ApiError
from api.graph import call_graph_api


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
def get_role(email: str) -> Optional[str]:
    """Return 'student', 'staff', or None for an invalid domain.

    NOTE: Student domain must be checked FIRST because '@students.ttu.ac.ke'
    also ends with '@ttu.ac.ke'."""
    if not email:
        return None
    email = email.strip().lower()
    if email.endswith(config.STUDENT_DOMAIN):
        return "student"
    if email.endswith(config.STAFF_DOMAIN):
        return "staff"
    return None


def require_role(email: str, role: str) -> str:
    """Validate the email has the expected role; raise ApiError otherwise."""
    actual = get_role(email)
    if actual is None:
        raise ApiError(400, {"error": "Invalid TTU email domain"})
    if actual != role:
        raise ApiError(403, {"error": f"This action requires a {role} account", "role": actual})
    return email.strip().lower()


async def resolve_user_id_by_email(email: str) -> str:
    """Resolve a UPN/email to a directory user id."""
    if not config.HAS_AZURE_CREDENTIALS:
        return f"mock-user-{email.split('@')[0]}"
    try:
        user = await call_graph_api(f"/users/{email}?$select=id,userPrincipalName,displayName,mail")
    except ApiError as e:
        if e.status_code == 404:
            raise ApiError(404, {"error": "User not found in directory", "email": email})
        raise
    return user["id"]


# ========== BULK MEMBER UPLOAD: FILE PARSING ==========
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_emailish(value: str) -> bool:
    return bool(value and _EMAIL_RE.match(value.strip()))


def parse_emails_from_upload(filename: str, content: bytes) -> list[tuple[int, str]]:
    """Parse an .xlsx/.xlsm/.csv upload into [(row_number, raw_email), ...].

    ``row_number`` is the 1-based spreadsheet row (header = 1) so the report can
    point staff back to the exact line. Blank rows are skipped. This function
    never raises on bad *data* — validation happens later per-row — but it does
    raise ApiError(400) on an unreadable/corrupt file.
    """
    ext = Path(filename or "").suffix.lower()
    if ext not in config.BULK_ALLOWED_EXT:
        raise ApiError(400, {
            "error": "Unsupported file type",
            "accepted": sorted(config.BULK_ALLOWED_EXT), "received": ext or "(none)",
        })

    rows: list[tuple[int, str]] = []

    try:
        if ext == ".csv":
            text = content.decode("utf-8-sig", errors="replace")
            # Sniff delimiter (handles ';' used by some locales).
            try:
                dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
            except Exception:
                dialect = csv.excel
            reader = csv.reader(io.StringIO(text), dialect)
            table = [r for r in reader]
            header_cells = table[0] if table else []
            col = _find_email_column([str(c) for c in header_cells], [str(c) for r in table[1:5] for c in r])
            start = 1 if col is not None and col < len(header_cells) else 0
        else:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            table = []
            for row in ws.iter_rows(values_only=True):
                table.append(["" if c is None else str(c) for c in row])
            header_cells = table[0] if table else []
            sample = [str(c) for r in table[1:5] for c in r]
            col = _find_email_column([str(c) for c in header_cells], sample)
            start = 1 if col is not None and col < len(header_cells) else 0
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(400, {"error": f"Could not read file: {e}"})

    if not table:
        return []

    if col is None:
        # No header found and no email-looking column → assume column 0.
        col = 0
        start = 0

    for i in range(start, len(table)):
        row = table[i]
        if col >= len(row):
            continue
        value = str(row[col]).strip()
        if not value:
            continue
        rows.append((i + 1, value))   # 1-based spreadsheet row number

    return rows


def _find_email_column(header_cells: list[str], sample_cells: list[str]) -> Optional[int]:
    """Locate the email column index by header name, then by heuristic."""
    # 1) Exact header match (case-insensitive, stripped).
    for idx, cell in enumerate(header_cells):
        if cell and cell.strip().lower() == config.BULK_EMAIL_COLUMN:
            return idx
    # 2) Loose header match (contains 'email', 'mail' or equals 'upn').
    for idx, cell in enumerate(header_cells):
        c = (cell or "").strip().lower()
        if "email" in c or "mail" in c or c == "upn":
            return idx
    # 3) Heuristic: first header/column value that looks like an email.
    for idx, cell in enumerate(header_cells):
        if _is_emailish(cell):
            return idx
    return None


def csv_safe(value: Any) -> str:
    """Neutralize spreadsheet formula injection in exported cells."""
    s = "" if value is None else str(value)
    if s[:1] in ("=", "+", "-", "@"):
        return "'" + s
    return s
