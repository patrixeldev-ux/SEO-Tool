"""Password hashing (argon2id), access-token JWTs and opaque refresh tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from searchos.core.config import Settings

# argon2-cffi's default type is argon2id with the RFC 9106 "low memory" parameters.
_hasher = PasswordHasher()

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"  # noqa: S105  (a claim value, not a secret)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# A real argon2id hash of a random string, verified against when the email is unknown so that
# "unknown user" and "wrong password" take the same time.
DUMMY_PASSWORD_HASH = _hasher.hash(secrets.token_urlsafe(16))


def create_access_token(user_id: uuid.UUID, settings: Settings, *, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "typ": ACCESS_TOKEN_TYPE,
        "iat": issued,
        "exp": issued + timedelta(minutes=settings.access_token_ttl_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)


class InvalidAccessTokenError(Exception):
    pass


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],  # never trust the token's own "alg"
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
        if claims.get("typ") != ACCESS_TOKEN_TYPE:
            raise InvalidAccessTokenError("wrong token type")
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidAccessTokenError(str(exc)) from exc


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 is appropriate here: the token is 384 bits of randomness, so no slow KDF is needed."""
    return hashlib.sha256(token.encode()).hexdigest()
