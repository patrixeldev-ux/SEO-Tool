from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from searchos.models import JobStatus, Role

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Password = Annotated[str, StringConstraints(min_length=10, max_length=128)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth -----------------------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    full_name: Name | None = None
    org_name: Name


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class RefreshRequest(BaseModel):
    """Body is optional: web clients send the httpOnly cookie; desktop clients send the token explicitly."""

    refresh_token: str | None = None


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str | None


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    role: Role


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    # Only populated for desktop clients (X-Client-Type: desktop); web gets an httpOnly cookie instead.
    refresh_token: str | None = None
    user: UserOut
    orgs: list[OrgOut]


class MeResponse(BaseModel):
    user: UserOut
    orgs: list[OrgOut]


class OrgCreate(BaseModel):
    name: Name


# ---- sites ----------------------------------------------------------------------------------------
class SiteCreate(BaseModel):
    domain: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    display_name: Name | None = None


class SiteUpdate(BaseModel):
    display_name: Name | None = None
    settings: dict[str, Any] | None = None
    autopilot_enabled: bool | None = None


class SiteOut(ORMModel):
    id: uuid.UUID
    org_id: uuid.UUID
    domain: str
    uses_www: bool
    display_name: str
    settings: dict[str, Any]
    autopilot_enabled: bool
    created_at: datetime


# ---- jobs -----------------------------------------------------------------------------------------
class JobOut(ORMModel):
    id: uuid.UUID
    org_id: uuid.UUID
    site_id: uuid.UUID | None
    type: str
    status: JobStatus
    progress: int
    error: str | None
    payload: dict[str, Any]
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class DemoJobRequest(BaseModel):
    steps: Annotated[int, Field(ge=1, le=20)] = 5
    step_seconds: Annotated[float, Field(ge=0, le=2)] = 0.2
    fail: bool = False
