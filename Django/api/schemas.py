"""Pydantic request models (ported unchanged from main.py)."""
from typing import Optional

from pydantic import BaseModel, EmailStr


class SelfRegisterRequest(BaseModel):
    email: str
    firstName: str
    lastName: str
    displayName: str
    licenseType: str
    password: str


class BulkRequest(BaseModel):
    operation: str
    userIds: list[str]
    data: Optional[dict] = None


class VerifyEmailRequest(BaseModel):
    email: EmailStr


class GroupActionRequest(BaseModel):
    email: EmailStr


class StaffGroupRequest(BaseModel):
    email: EmailStr
    displayName: str
    description: Optional[str] = ""
    mailNickname: Optional[str] = None
