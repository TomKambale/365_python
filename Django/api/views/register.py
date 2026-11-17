"""Self-registration: create the Azure AD user, assign licenses, send emails."""
import logging
from datetime import datetime

from api import config
from api.email_service import email_service
from api.exceptions import ApiError
from api.graph import call_graph_api
from api.mock_data import LICENSE_SKU_MAP, mock_users
from api.schemas import SelfRegisterRequest
from api.views.common import api_view, json_response, parse_model

log = logging.getLogger("azure-admin")


@api_view("POST")
async def self_register(request):
    req = parse_model(SelfRegisterRequest, request)

    # Validate license type
    if req.licenseType not in LICENSE_SKU_MAP:
        raise ApiError(400, {
            "error": "Invalid license type",
            "validTypes": list(LICENSE_SKU_MAP.keys()),
        })
    # Validate email domain
    valid_domains = ("@students.ttu.ac.ke", "@ttu.ac.ke")
    if not req.email.endswith(valid_domains):
        raise ApiError(400, {
            "error": "Invalid email domain",
            "message": "Email must be from @students.ttu.ac.ke or @ttu.ac.ke",
        })

    if not config.HAS_AZURE_CREDENTIALS:
        mock_id = f"mock-{int(datetime.utcnow().timestamp() * 1000)}"
        mock_users.append({
            "id": mock_id, "displayName": req.displayName,
            "userPrincipalName": req.email, "mail": req.email,
            "givenName": req.firstName, "surname": req.lastName,
            "accountEnabled": True,
            "createdDateTime": datetime.utcnow().isoformat(),
        })
        return json_response({
            "success": True, "userId": mock_id, "email": req.email,
            "displayName": req.displayName,
            "licenseAssigned": LICENSE_SKU_MAP[req.licenseType]["displayName"],
            "message": "Account created successfully (mock mode)",
            "timestamp": datetime.utcnow().isoformat(),
        }, status=201)

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
    except ApiError as e:
        if "already exists" in str(e.detail):
            raise ApiError(409, {"success": False, "error": "User already exists"})
        raise

    # ---- Assign license(s) ----
    license_assigned = None          # first successful label (backwards compat)
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
        skipped = []            # ["label: reason"]
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

    return json_response({
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
    }, status=201)
