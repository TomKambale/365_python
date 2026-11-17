"""License endpoints."""
from datetime import datetime

from api import config
from api.exceptions import ApiError
from api.graph import call_graph_api
from api.mock_data import mock_licenses
from api.views.common import api_view, json_response, parse_json_body


@api_view("GET")
async def list_licenses(request):
    if config.HAS_AZURE_CREDENTIALS:
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
        return json_response({"value": value, "count": len(value),
                              "timestamp": datetime.utcnow().isoformat()})
    return json_response({"value": mock_licenses, "count": len(mock_licenses),
                          "timestamp": datetime.utcnow().isoformat()})


@api_view("GET")
async def get_license(request, license_id: str):
    if config.HAS_AZURE_CREDENTIALS:
        licenses = await call_graph_api("/subscribedSkus")
        for l in licenses.get("value", []):
            if l["skuPartNumber"] == license_id:
                return json_response(l)
        raise ApiError(404, "License not found")
    for l in mock_licenses:
        if l["id"] == license_id:
            return json_response(l)
    raise ApiError(404, "License not found")


@api_view("POST")
async def assign_license(request, license_id: str):
    body = parse_json_body(request)
    user_id = body.get("userId")
    if not user_id:
        raise ApiError(400, "userId is required")
    if config.HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/users/{user_id}/assignLicense", "POST", {
            "addLicenses": [{"skuId": license_id}],
            "removeLicenses": [],
        })
    return json_response({"message": "License assigned successfully"})


@api_view("POST")
async def remove_license(request, license_id: str):
    body = parse_json_body(request)
    user_id = body.get("userId")
    if not user_id:
        raise ApiError(400, "userId is required")
    if config.HAS_AZURE_CREDENTIALS:
        await call_graph_api(f"/users/{user_id}/assignLicense", "POST", {
            "addLicenses": [],
            "removeLicenses": [license_id],
        })
    return json_response({"message": "License removed successfully"})


@api_view("GET")
async def get_user_licenses(request, user_id: str):
    if config.HAS_AZURE_CREDENTIALS:
        licenses = await call_graph_api(f"/users/{user_id}/licenseDetails")
        value = [{
            "skuId": l["skuId"],
            "skuPartNumber": l["skuPartNumber"],
            "productName": l["skuPartNumber"],
            "assigned": True,
        } for l in licenses.get("value", [])]
        return json_response({"value": value, "count": len(value),
                              "timestamp": datetime.utcnow().isoformat()})
    return json_response({"value": [{**l, "assigned": True} for l in mock_licenses[:2]],
                          "count": 2, "timestamp": datetime.utcnow().isoformat()})
