"""Which deployer serves which method. Tests replace entries to inject fakes."""

from __future__ import annotations

from searchos.deploy.base import Deployer
from searchos.models import DeployMethod

_DEPLOYERS: dict[DeployMethod, Deployer] = {}


def register(deployer: Deployer) -> None:
    _DEPLOYERS[deployer.method] = deployer


def get_deployer(method: DeployMethod) -> Deployer:
    if not _DEPLOYERS:
        _load_defaults()
    try:
        return _DEPLOYERS[method]
    except KeyError:
        raise LookupError(f"no deployer for {method.value}") from None


def _load_defaults() -> None:
    # imported here so importing the registry never pulls in every client library
    from searchos.deploy.cloudflare import CloudflareDeployer

    for d in (CloudflareDeployer(),):
        _DEPLOYERS.setdefault(d.method, d)
