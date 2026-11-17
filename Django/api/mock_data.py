"""In-memory mock data served when Azure credentials are absent, plus the
tenant-specific license SKU map (ported unchanged from main.py)."""

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

# SKU GUIDs below are tied to the real TTU tenant.
LICENSE_SKU_MAP = {
    "Student": {
        "displayName": "Microsoft 365 A1 for Students",
        "skus": [
            {
                "skuId": "314c4481-f395-4525-be8b-2ec4bb1e9d91",
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
                "skuId": "94763226-9b3c-4e75-a931-5c89701abe66",
                "skuPartNumber": "STANDARDWOFFPACK_FACULTY",
                "label": "M365 A3 Faculty",
                "required": True,
            },
            {
                "skuId": "a403ebcc-fae0-4ca2-8c8c-7a907fd6c235",
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
                "skuId": "f30db892-07e9-47e9-837c-80727f46fd3d",
                "skuPartNumber": "FLOW_FREE",
                "label": "Flow Free",
                "required": False,   # optional: don't fail the whole thing if this fails
            },
        ],
    },
}
