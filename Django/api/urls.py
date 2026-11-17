"""API routes — mirrors the FastAPI paths exactly (no trailing slashes).

Order matters: literal segments ("users/bulk", "groups/owned") must come
before the parameterized catch-alls.
"""
from django.urls import path

from api.views import groups, health, licenses, register, staff_bulk, users

urlpatterns = [
    # Health & stats
    path("health", health.health),
    path("stats", health.stats),

    # Email / role verification
    path("verify-email", users.verify_email),

    # Users (literal paths before <user_id>)
    path("users/bulk", users.bulk_users),
    path("users/import", users.import_users),
    path("users/<str:user_id>/licenses", licenses.get_user_licenses),
    path("users/<str:user_id>", users.user_detail),
    path("users", users.users_collection),

    # Staff bulk member upload
    path("staff/groups/members/template", staff_bulk.bulk_members_template),
    path("staff/groups/<str:group_id>/members/bulk/report", staff_bulk.bulk_add_members_report),
    path("staff/groups/<str:group_id>/members/bulk", staff_bulk.bulk_add_members),

    # Groups (literal paths before <group_id>)
    path("groups/owned", groups.list_owned_groups),
    path("students/<str:email>/groups", groups.list_student_groups),
    path("groups/<str:group_id>/members/<str:member_id>", groups.remove_group_member),
    path("groups/<str:group_id>/members", groups.group_members),
    path("groups/<str:group_id>/owners", groups.group_owners),
    path("groups/<str:group_id>/join", groups.join_group),
    path("groups/<str:group_id>/leave", groups.leave_group),
    path("groups/<str:group_id>", groups.group_detail),
    path("groups", groups.groups_collection),

    # Licenses
    path("licenses/<str:license_id>/assign", licenses.assign_license),
    path("licenses/<str:license_id>/remove", licenses.remove_license),
    path("licenses/<str:license_id>", licenses.get_license),
    path("licenses", licenses.list_licenses),

    # Self-registration
    path("self-register", register.self_register),
]
