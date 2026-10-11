"""Authentication service: registration, login, refresh-token rotation with reuse detection, logout."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core import security
from searchos.core.config import Settings
from searchos.models import Membership, Org, RefreshToken, Role, User


class AuthenticationError(Exception):
    """Credentials or token rejected. The message is for logs, never for the client."""


class RefreshTokenReuseError(AuthenticationError):
    """A rotated (already used) or revoked refresh token was presented; its family has been revoked."""


class EmailAlreadyRegisteredError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int  # seconds, access token lifetime


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _now() -> datetime:
    return datetime.now(UTC)


def _issue_refresh_token(
    session: AsyncSession, settings: Settings, user_id: uuid.UUID, family_id: uuid.UUID
) -> str:
    raw = security.new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user_id,
            family_id=family_id,
            token_hash=security.hash_refresh_token(raw),
            expires_at=_now() + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    return raw


def _issue_tokens(
    session: AsyncSession, settings: Settings, user_id: uuid.UUID, family_id: uuid.UUID
) -> IssuedTokens:
    return IssuedTokens(
        access_token=security.create_access_token(user_id, settings),
        refresh_token=_issue_refresh_token(session, settings, user_id, family_id),
        expires_in=settings.access_token_ttl_minutes * 60,
    )


async def register(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    full_name: str | None,
    org_name: str,
) -> tuple[User, Org, IssuedTokens]:
    """Create a user, their organisation and an owner membership, and log them in."""
    email = normalize_email(email)
    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise EmailAlreadyRegisteredError(email)

    user = User(email=email, password_hash=security.hash_password(password), full_name=full_name)
    org = Org(name=org_name)
    session.add_all([user, org])
    await session.flush()
    session.add(Membership(org_id=org.id, user_id=user.id, role=Role.owner))
    tokens = _issue_tokens(session, settings, user.id, uuid.uuid4())
    try:
        await session.commit()
    except IntegrityError as exc:  # lost a race with a concurrent registration of the same email
        await session.rollback()
        raise EmailAlreadyRegisteredError(email) from exc
    return user, org, tokens


async def login(
    session: AsyncSession, settings: Settings, *, email: str, password: str
) -> tuple[User, IssuedTokens]:
    user = await session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        # Spend the same time as a real verification so response time does not reveal which emails exist.
        security.verify_password(security.DUMMY_PASSWORD_HASH, password)
        raise AuthenticationError("unknown email")
    if not security.verify_password(user.password_hash, password):
        raise AuthenticationError("wrong password")
    if not user.is_active:
        raise AuthenticationError("inactive user")
    if security.password_needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
    tokens = _issue_tokens(session, settings, user.id, uuid.uuid4())
    await session.commit()
    return user, tokens


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def refresh(session: AsyncSession, settings: Settings, *, raw_token: str) -> tuple[User, IssuedTokens]:
    """Exchange a refresh token for a new access + refresh token pair (single use, rotating).

    If a token that was already rotated (or revoked) is presented again, assume theft: the entire
    token family is revoked, so the legitimate holder's newest token stops working too and must log in again.
    """
    now = _now()
    row = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == security.hash_refresh_token(raw_token))
        .with_for_update()
    )
    if row is None:
        raise AuthenticationError("unknown refresh token")

    if row.used_at is not None or row.revoked_at is not None:
        await _revoke_family(session, row.family_id, now)
        await session.commit()  # persist the revocation even though we are about to reject the request
        raise RefreshTokenReuseError("refresh token reuse detected")

    if row.expires_at <= now:
        raise AuthenticationError("refresh token expired")

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("user inactive")

    row.used_at = now
    tokens = _issue_tokens(session, settings, user.id, row.family_id)
    await session.commit()
    return user, tokens


async def logout(session: AsyncSession, *, raw_token: str | None) -> None:
    """Revoke the whole token family of the presented refresh token. Unknown tokens are ignored."""
    if not raw_token:
        return
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == security.hash_refresh_token(raw_token))
    )
    if row is not None:
        await _revoke_family(session, row.family_id, _now())
        await session.commit()


async def purge_expired_refresh_tokens(session: AsyncSession, *, older_than_days: int = 7) -> int:
    """Housekeeping (run by celery beat): delete tokens that expired more than `older_than_days` ago."""
    cutoff = _now() - timedelta(days=older_than_days)
    result = await session.execute(delete(RefreshToken).where(RefreshToken.expires_at < cutoff))
    await session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
