"""Health & stats endpoints."""
from datetime import datetime

from api import config
from api.graph import call_graph_api
from api.mock_data import mock_groups, mock_licenses, mock_users
from api.views.common import api_view, json_response


@api_view("GET")
async def health(request):
    return json_response({
        "status": "OK",
        "service": "Azure Admin API",
        "timestamp": datetime.utcnow().isoformat(),
        "usingGraphAPI": config.HAS_AZURE_CREDENTIALS,
        "version": "1.0.0",
    })


@api_view("GET")
async def stats(request):
    user_count = group_count = license_count = 0
    if config.HAS_AZURE_CREDENTIALS:
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
    return json_response({
        "users": user_count,
        "groups": group_count,
        "licenses": license_count,
        "timestamp": datetime.utcnow().isoformat(),
    })
