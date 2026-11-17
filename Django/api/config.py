"""Environment-derived configuration (ported from the top of main.py).

The .env files are loaded by azureadmin.settings before this module imports.
"""
import os

AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "").strip()
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "").strip()
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "").strip()

# If any credential is missing the app silently serves mock data instead of
# calling Microsoft Graph (same behavior as the FastAPI app).
HAS_AZURE_CREDENTIALS = all([AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_CLIENT_SECRET])

# ===== TTU domains =====
STUDENT_DOMAIN = "@students.ttu.ac.ke"
STAFF_DOMAIN = "@ttu.ac.ke"

# ===== Cache TTLs (seconds) =====
GROUPS_CACHE_TTL = int(os.getenv("GROUPS_CACHE_TTL", "60"))
MEMBERS_CACHE_TTL = int(os.getenv("MEMBERS_CACHE_TTL", "45"))
OWNERS_CACHE_TTL = int(os.getenv("OWNERS_CACHE_TTL", "120"))
USER_ID_CACHE_TTL = int(os.getenv("USER_ID_CACHE_TTL", "600"))

# Max simultaneous Graph requests — protects the tenant & the local process.
GRAPH_CONCURRENCY = int(os.getenv("GRAPH_CONCURRENCY", "8"))

# ===== Bulk member upload =====
BULK_ALLOWED_EXT = {".xlsx", ".xlsm", ".csv"}
BULK_MAX_BYTES = int(os.getenv("BULK_MAX_BYTES", str(5 * 1024 * 1024)))   # 5 MB
BULK_MAX_ROWS = int(os.getenv("BULK_MAX_ROWS", "1000"))                   # hard cap / upload
BULK_ADD_CONCURRENCY = int(os.getenv("BULK_ADD_CONCURRENCY", "5"))        # parallel Graph writes
BULK_EMAIL_COLUMN = os.getenv("BULK_EMAIL_COLUMN", "email").strip().lower()
