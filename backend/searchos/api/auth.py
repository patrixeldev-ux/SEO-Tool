from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.api.schemas import (
    LoginRequest,
    MeResponse,
    OrgOut,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from searchos.core import auth as auth_service
from searchos.core.config import Settings
from searchos.core.deps import CurrentUser, SessionDep, SettingsDep
from searchos.models import Membership, Org, User

router = APIRouter(prefix="/auth", tags=["auth"])

DESKTOP_CLIENT = "desktop"


async def _orgs_for(session: AsyncSession, user_id: object) -> list[OrgOut]:
    rows = await session.execute(
        sa.select(Org.id, Org.name, Membership.role)
        .join(Membership, Membership.org_id == Org.id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at, Membership.id)
    )
    return [OrgOut(id=r.id, name=r.name, role=r.role) for r in rows]


def _set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_ttl_days * 86400,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",
    )


def _is_desktop(client_type: str | None) -> bool:
    return (client_type or "").strip().lower() == DESKTOP_CLIENT


async def _token_response(
    session: AsyncSession,
    settings: Settings,
    response: Response,
    user: User,
    tokens: auth_service.IssuedTokens,
    *,
    desktop: bool,
) -> TokenResponse:
    if desktop:
        refresh_in_body: str | None = tokens.refresh_token  # desktop stores it in the OS keychain
    else:
        _set_refresh_cookie(response, settings, tokens.refresh_token)
        refresh_in_body = None
    return TokenResponse(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        refresh_token=refresh_in_body,
        user=UserOut.model_validate(user),
        orgs=await _orgs_for(session, user.id),
    )


def _reject_foreign_origin(request: Request, settings: Settings) -> None:
    """Defence in depth for cookie-authenticated endpoints (on top of SameSite=Lax + CORS)."""
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in settings.cors_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin not allowed")


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    x_client_type: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    try:
        user, _org, tokens = await auth_service.register(
            session,
            settings,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            org_name=body.org_name,
        )
    except auth_service.EmailAlreadyRegisteredError:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists") from None
    return await _token_response(
        session, settings, response, user, tokens, desktop=_is_desktop(x_client_type)
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    x_client_type: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    try:
        user, tokens = await auth_service.login(session, settings, email=body.email, password=body.password)
    except auth_service.AuthenticationError:
        # One generic message for unknown email, wrong password and inactive account.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password") from None
    return await _token_response(
        session, settings, response, user, tokens, desktop=_is_desktop(x_client_type)
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    body: RefreshRequest | None = None,
    x_client_type: Annotated[str | None, Header()] = None,
) -> TokenResponse | JSONResponse:
    desktop = _is_desktop(x_client_type)
    raw = body.refresh_token if body and body.refresh_token else None
    if raw is None:
        raw = request.cookies.get(settings.refresh_cookie_name)  # cookie flow (web)
        if raw is not None:
            _reject_foreign_origin(request, settings)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing refresh token")
    try:
        user, tokens = await auth_service.refresh(session, settings, raw_token=raw)
    except auth_service.AuthenticationError:
        # Headers set on the injected `response` are dropped when an HTTPException is raised,
        # so build the 401 ourselves in order to clear the dead cookie.
        failure = JSONResponse(
            {"detail": "Invalid or expired refresh token"}, status_code=status.HTTP_401_UNAUTHORIZED
        )
        _clear_refresh_cookie(failure, settings)
        return failure
    return await _token_response(session, settings, response, user, tokens, desktop=desktop)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    body: RefreshRequest | None = None,
) -> None:
    raw = body.refresh_token if body and body.refresh_token else None
    if raw is None:
        raw = request.cookies.get(settings.refresh_cookie_name)
        if raw is not None:
            _reject_foreign_origin(request, settings)
    await auth_service.logout(session, raw_token=raw)
    _clear_refresh_cookie(response, settings)


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser, session: SessionDep) -> MeResponse:
    return MeResponse(user=UserOut.model_validate(user), orgs=await _orgs_for(session, user.id))
