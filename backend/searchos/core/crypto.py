"""Encryption of stored credentials (OAuth tokens, API keys) with Fernet.

The key comes from `TOKEN_ENCRYPTION_KEY`. In development and test it is derived from `JWT_SECRET` when unset
(logged once as a warning). In production there is no fallback: credentials are never stored unencrypted, so
without a key the integration endpoints answer "not configured".
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from searchos.core.config import Settings, get_settings

log = logging.getLogger(__name__)
_warned = False


class EncryptionUnavailableError(RuntimeError):
    """No usable encryption key: credentials must not be stored."""


class DecryptionError(RuntimeError):
    """Stored credentials could not be decrypted (wrong key, or data corrupted)."""


def _derive_from_secret(secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(b"searchos-token-key:" + secret.encode()).digest())


def get_fernet(settings: Settings | None = None) -> Fernet:
    global _warned
    settings = settings or get_settings()
    if settings.token_encryption_key is not None:
        try:
            return Fernet(settings.token_encryption_key.get_secret_value().encode())
        except ValueError as exc:
            raise EncryptionUnavailableError(
                "TOKEN_ENCRYPTION_KEY is not a valid Fernet key (32 url-safe base64 bytes). Make one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            ) from exc
    if settings.is_production:
        raise EncryptionUnavailableError("TOKEN_ENCRYPTION_KEY is not set; credentials cannot be stored.")
    if not _warned:
        log.warning(
            "TOKEN_ENCRYPTION_KEY is not set: deriving an encryption key from JWT_SECRET (development only)."
        )
        _warned = True
    return Fernet(_derive_from_secret(settings.jwt_secret.get_secret_value()))


def encryption_available(settings: Settings | None = None) -> bool:
    try:
        get_fernet(settings)
    except EncryptionUnavailableError:
        return False
    return True


def encrypt_json(data: dict[str, Any], settings: Settings | None = None) -> str:
    return get_fernet(settings).encrypt(json.dumps(data, separators=(",", ":")).encode()).decode()


def decrypt_json(token: str, settings: Settings | None = None, *, ttl: int | None = None) -> dict[str, Any]:
    try:
        raw = get_fernet(settings).decrypt(token.encode(), ttl=ttl)
    except InvalidToken as exc:
        raise DecryptionError("stored credentials cannot be decrypted") from exc
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise DecryptionError("stored credentials have an unexpected shape")
    return data
