"""Errors shared by the integration clients."""

from __future__ import annotations


class IntegrationError(Exception):
    """Base class. `str(error)` is safe to show to the owner (no tokens, no keys)."""


class NotConfiguredError(IntegrationError):
    """The server has no credentials for this provider (for example GOOGLE_CLIENT_ID is unset)."""


class NeedsReauthError(IntegrationError):
    """Google rejected the stored refresh token (invalid_grant): the owner has to connect again."""


class ProviderApiError(IntegrationError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
