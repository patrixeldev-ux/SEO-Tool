"""What a deployer is asked to do: the safety gate and the mapping from an approved action to page operations.

The page operations ("ops") are the one contract shared by every deployer. They are plain JSON:

    {"op": "title", "value": "..."}
    {"op": "meta_description", "value": "..."}
    {"op": "canonical", "value": "https://..."}
    {"op": "alt", "src": "https://.../logo.png", "value": "..."}
    {"op": "jsonld", "id": "searchos-ab12cd34ef56", "value": {...}, "replace": {"types": [...]} | "invalid" | null}
    {"op": "link", "id": "ab12cd34-0", "anchor": "coffee grinders", "to": "https://.../grinders"}

The Cloudflare Worker (edge/), the WordPress plugin and the GitHub static-HTML editor all implement exactly
these. Nothing else can be deployed: an action type without ops here is a manual hand-off.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any, Final

from searchos.audit.schema_rules import (
    ARTICLE_FAMILY,
    LOCAL_BUSINESS_TYPES,
    ORGANIZATION_TYPES,
    RULES,
)
from searchos.planner.catalog import NEVER_AUTOMATED, RiskClass, may_auto_deploy

# Action type -> what it edits. Anything not listed here cannot be deployed by any deployer.
DEPLOYABLE_TYPES: Final = frozenset(
    {
        "rewrite_title",
        "rewrite_meta",
        "add_alt",
        "add_jsonld",
        "fix_jsonld",
        "add_internal_links",
        "fix_canonical",
    }
)
MAX_TEXT = 600
MAX_JSONLD_BYTES = 20_000
MAX_LINKS_PER_ACTION = 3


class NotDeployableError(Exception):
    """The action may not be deployed (by this route). `str(error)` is a sentence for the person."""


def assert_deployable(action_type: str, risk_class: RiskClass | str, *, via_autopilot: bool) -> None:
    """The second lock behind the planner: never-automated things and manual tasks stop here whatever the status.

    * the never-automated list is refused on every route (even a person's click);
    * manual tasks and types without page operations are hand-offs, not deployments;
    * autopilot may only deploy the `auto` class.
    """
    if action_type in NEVER_AUTOMATED:
        raise NotDeployableError("This kind of change is never made automatically. Do it by hand.")
    risk = RiskClass(risk_class)
    if risk is RiskClass.manual:
        raise NotDeployableError("This is a manual task; it cannot be deployed.")
    if action_type not in DEPLOYABLE_TYPES:
        raise NotDeployableError(
            "This type of change has no automatic deployer yet; use the developer hand-off instead."
        )
    if via_autopilot and not may_auto_deploy(action_type):
        raise NotDeployableError(
            "Autopilot only deploys low-risk changes; this one needs a person to deploy it."
        )
    if via_autopilot and risk is not RiskClass.auto:
        raise NotDeployableError(
            "Autopilot only deploys low-risk changes; this one needs a person to deploy it."
        )


def jsonld_family_types(family: str) -> list[str]:
    """Every type name that the audit treats as belonging to a rule family (Organization, LocalBusiness, ...)."""
    if family == "Article":
        names = set(ARTICLE_FAMILY)
    elif family == "LocalBusiness":
        names = set(LOCAL_BUSINESS_TYPES)
    elif family == "Organization":
        names = set(ORGANIZATION_TYPES) - set(LOCAL_BUSINESS_TYPES) | {"Organization"}
    elif family in RULES:
        names = {family}
    else:
        names = {family}
    return sorted(names)


def jsonld_dom_id(action_id: uuid.UUID) -> str:
    return f"searchos-{action_id.hex[:12]}"


def _text(value: Any, what: str, *, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NotDeployableError(f"The proposed {what} is empty.")
    if len(value) > limit:
        raise NotDeployableError(f"The proposed {what} is too long to deploy.")
    return value.strip()


def _http_url(value: Any, what: str) -> str:
    text = _text(value, what, limit=2000)
    if not text.startswith(("http://", "https://")):
        raise NotDeployableError(f"The {what} must be a full http(s) address.")
    return text


def jsonld_value(raw: Any) -> dict[str, Any]:
    entity = raw
    if isinstance(raw, str):
        try:
            entity = json.loads(raw)
        except ValueError:
            raise NotDeployableError("The proposed structured data is not valid JSON.") from None
    if not isinstance(entity, dict) or "@type" not in entity:
        raise NotDeployableError("The proposed structured data must be one JSON-LD object with an @type.")
    if len(json.dumps(entity, ensure_ascii=False)) > MAX_JSONLD_BYTES:
        raise NotDeployableError("The proposed structured data is too large to deploy.")
    return entity


def ops_for_action(action_type: str, field: str, proposed: Any, action_id: uuid.UUID) -> list[dict[str, Any]]:
    """The page operations for one approved action. Raises NotDeployableError for a missing or bad value."""
    if proposed is None:
        raise NotDeployableError("This action has no proposed value to deploy.")
    if action_type == "rewrite_title":
        return [{"op": "title", "value": _text(proposed, "title")}]
    if action_type == "rewrite_meta":
        return [{"op": "meta_description", "value": _text(proposed, "meta description")}]
    if action_type == "fix_canonical":
        return [{"op": "canonical", "value": _http_url(proposed, "canonical address")}]
    if action_type == "add_alt":
        if not field.startswith("alt:"):
            raise NotDeployableError("The image of this action is unknown.")
        return [
            {
                "op": "alt",
                "src": _http_url(field[4:], "image address"),
                "value": _text(proposed, "alt text", limit=300),
            }
        ]
    if action_type in ("add_jsonld", "fix_jsonld"):
        entity = jsonld_value(proposed)
        replace: Any = None
        if action_type == "fix_jsonld":
            family = field.split(":", 1)[1] if ":" in field else ""
            replace = "invalid" if family == "parse_error" else {"types": jsonld_family_types(family)}
        return [{"op": "jsonld", "id": jsonld_dom_id(action_id), "value": entity, "replace": replace}]
    if action_type == "add_internal_links":
        links = proposed.get("links") if isinstance(proposed, Mapping) else None
        if not isinstance(links, list) or not links:
            raise NotDeployableError("This action has no links to add.")
        out: list[dict[str, Any]] = []
        for i, link in enumerate(links[:MAX_LINKS_PER_ACTION]):
            if not isinstance(link, Mapping):
                raise NotDeployableError("A proposed link is malformed.")
            out.append(
                {
                    "op": "link",
                    "id": f"{action_id.hex[:8]}-{i}",
                    "anchor": _text(link.get("anchor"), "link text", limit=120),
                    "to": _http_url(link.get("to"), "link target"),
                }
            )
        return out
    raise NotDeployableError("This type of change has no automatic deployer yet.")
