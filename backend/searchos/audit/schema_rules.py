"""Which structured-data properties we require per schema.org type (our own table, used by
SCHEMA_REQUIRED_PROP_MISSING and, in Phase 3, to validate generated JSON-LD).

`required` must all be present and non-empty. Each tuple in `one_of` needs at least one member present.
These are deliberately a little stricter than Google's bare minimum where the extra property is what makes
the markup useful to AI assistants (for example an Organization needs a url).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TypeRule:
    required: tuple[str, ...] = ()
    one_of: tuple[tuple[str, ...], ...] = ()


# Sub-types (LocalBusiness -> Restaurant, Organization -> Corporation, ...) are mapped to their family.
ORGANIZATION_TYPES = frozenset(
    {
        "Organization",
        "Corporation",
        "NGO",
        "OnlineBusiness",
        "OnlineStore",
        "EducationalOrganization",
        "GovernmentOrganization",
        "MedicalOrganization",
        "SportsOrganization",
        "LocalBusiness",
        "ProfessionalService",
        "Store",
        "Restaurant",
        "CafeOrCoffeeShop",
        "FoodEstablishment",
        "LodgingBusiness",
        "HealthAndBeautyBusiness",
        "AutomotiveBusiness",
        "FinancialService",
        "LegalService",
        "RealEstateAgent",
        "TravelAgency",
        "Dentist",
        "Physician",
    }
)
LOCAL_BUSINESS_TYPES = frozenset(
    ORGANIZATION_TYPES
    - {
        "Organization",
        "Corporation",
        "NGO",
        "OnlineBusiness",
        "OnlineStore",
        "EducationalOrganization",
        "GovernmentOrganization",
        "MedicalOrganization",
        "SportsOrganization",
    }
)
ARTICLE_FAMILY = frozenset({"Article", "NewsArticle", "BlogPosting", "TechArticle", "ScholarlyArticle"})

RULES: dict[str, TypeRule] = {
    "Organization": TypeRule(required=("name", "url")),
    "LocalBusiness": TypeRule(required=("name", "address")),
    "Article": TypeRule(required=("headline", "author", "datePublished")),
    "Product": TypeRule(required=("name",), one_of=(("offers", "review", "aggregateRating"),)),
    "FAQPage": TypeRule(required=("mainEntity",)),
    "BreadcrumbList": TypeRule(required=("itemListElement",)),
}


def types_of(entity: dict[str, Any]) -> list[str]:
    raw = entity.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return [str(v).rsplit("/", 1)[-1] for v in values if isinstance(v, str)]


def rule_family(schema_type: str) -> str | None:
    """The key in RULES that applies to a type, or None when we do not check it."""
    if schema_type in ARTICLE_FAMILY:
        return "Article"
    if schema_type in LOCAL_BUSINESS_TYPES:
        return "LocalBusiness"
    if schema_type in ORGANIZATION_TYPES:
        return "Organization"
    return schema_type if schema_type in RULES else None


def is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def missing_properties(entity: dict[str, Any], family: str) -> list[str]:
    rule = RULES[family]
    missing = [prop for prop in rule.required if not is_present(entity.get(prop))]
    for group in rule.one_of:
        if not any(is_present(entity.get(prop)) for prop in group):
            missing.append(" or ".join(group))
    return missing


def has_organization(entities: list[dict[str, Any]]) -> bool:
    return any(t in ORGANIZATION_TYPES for e in entities for t in types_of(e))
