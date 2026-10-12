"""Manual tasks: the work that earns trust and links, which no tool can or should do for a person.

Four task types, all risk class `manual` (never automated; Search OS does not email anyone, post anywhere or
touch a page for them): `earn_links`, `author_eeat`, `collect_reviews`, `digital_pr`. Each is one action per site
with a checklist. The checklists are written in plain language and use the site's own data where there is some:
the pages most worth linking to, the queries people already find the site by, whether an About page exists,
which pages lack a byline.

The value is `{"kind": "checklist", "title", "intro", "items": [{"id", "text", "detail"?}], "never": [..]}`.
Item ids are stable, so a re-plan can refresh the wording without losing which boxes were ticked
(`actions.checklist_done`).

Search OS never suggests buying links, swapping links, mass outreach, writing reviews or inventing credentials;
`NEVER` below is shown with every checklist that touches those topics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from searchos.audit.schema_rules import types_of
from searchos.crawl.urls import match_key
from searchos.planner.mapping import MAPPINGS
from searchos.planner.model import Candidate, Finding, PlanContext, PlanPage

MAX_ASSETS = 5
MAX_TOPICS = 5
MAX_URLS_LISTED = 100
MIN_ASSET_WORDS = 300

# Pages that exist for the site's operation, not to be cited by others.
UTILITY_PATH = re.compile(
    r"/(cart|basket|checkout|login|log-in|signin|sign-in|register|account|privacy|terms|cookie|legal|imprint|"
    r"contact|search|tag|tags|category|categories|thank-you|404|unsubscribe)(/|$|\.|-)",
    re.I,
)
ABOUT_PATH = re.compile(
    r"/(about|about-us|team|our-team|our-story|who-we-are|company|authors?|people|staff)(/|$|\.)", re.I
)
CONTACT_PATH = re.compile(r"/(contact|contact-us|get-in-touch|support)(/|$|\.)", re.I)

STANDALONE_TYPES: tuple[str, ...] = ("earn_links", "collect_reviews", "digital_pr")

NEVER_LINKS = [
    'Buy links, or accept money for a link that is not marked rel="sponsored".',
    'Swap links ("link to me and I will link to you") or join link-exchange groups or private blog networks.',
    "Send the same message to hundreds of strangers, or post links in comments and forums to be seen by search engines.",
]
NEVER_REVIEWS = [
    "Write, buy or edit reviews, or ask staff, friends or family to post them.",
    "Offer a reward for a positive review, or ask only your happiest customers.",
]
NEVER_TRUST = [
    "Invent a name, a credential, an award or a number of years in business. Say only what is true and can be checked.",
]
NEVER_PR = [
    "Publish made-up or exaggerated statistics. If a journalist checks and finds a gap, the damage lasts.",
    "Pay for a placement and present it as editorial coverage.",
]


# ---- site facts ------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Asset:
    url: str
    title: str
    words: int
    impressions: int
    clicks: int
    inlinks: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "words": self.words,
            "impressions_28d": self.impressions,
            "clicks_28d": self.clicks,
            "inlinks": self.inlinks,
        }


def _healthy(page: PlanPage) -> bool:
    return page.is_indexable and page.status_code == 200 and page.has_text


def linkable_assets(ctx: PlanContext, limit: int = MAX_ASSETS) -> list[Asset]:
    """The pages other sites would most plausibly cite: substantial, indexable, not the home or utility pages.

    Ranked by Search Console impressions (people already look for them), then by length."""
    found: list[Asset] = []
    for url, page in ctx.pages.items():
        if not _healthy(page) or url == ctx.homepage_url or UTILITY_PATH.search(url):
            continue
        if page.word_count < MIN_ASSET_WORDS:
            continue
        entry = ctx.gsc.get(match_key(url))
        found.append(
            Asset(
                url,
                (page.h1[0] if page.h1 else page.title) or url,
                page.word_count,
                entry.impressions if entry else 0,
                entry.clicks if entry else 0,
                page.inlinks,
            )
        )
    found.sort(key=lambda a: (-a.impressions, -a.words, a.url))
    return found[:limit]


def top_queries(ctx: PlanContext, limit: int = MAX_TOPICS) -> list[dict[str, Any]]:
    """The queries with the most impressions across the site (Search Console, newest 28 days)."""
    totals: dict[str, list[int]] = {}
    for entry in ctx.gsc.values():
        for q in entry.queries:
            row = totals.setdefault(q.query, [0, 0])
            row[0] += q.impressions
            row[1] += q.clicks
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return [{"query": q, "impressions_28d": v[0], "clicks_28d": v[1]} for q, v in ranked[:limit]]


def topic_of(ctx: PlanContext, assets: list[Asset]) -> str:
    """What the site is about, in the site's own words: its best query, else its best page's heading."""
    queries = top_queries(ctx, 1)
    if queries:
        return str(queries[0]["query"])
    if assets:
        return assets[0].title
    return ctx.site_name or ctx.domain


def schema_types(ctx: PlanContext) -> set[str]:
    out: set[str] = set()
    for page in ctx.pages.values():
        for entity in page.jsonld:
            out.update(types_of(entity))
    return out


def find_pages(ctx: PlanContext, pattern: re.Pattern[str]) -> list[str]:
    return sorted(u for u, p in ctx.pages.items() if _healthy(p) and pattern.search(u))


def brand(ctx: PlanContext) -> str:
    name = (ctx.site_name or "").strip()
    return name if name and "." not in name else ctx.domain


def item(item_id: str, text: str, detail: str | None = None) -> dict[str, str]:
    row = {"id": item_id, "text": text}
    if detail:
        row["detail"] = detail
    return row


def _why(source: str) -> list[str]:
    mapping = MAPPINGS.get(source)
    return [mapping.why] if mapping and mapping.why else []


def _checklist(title: str, intro: str, items: list[dict[str, str]], never: list[str]) -> dict[str, Any]:
    return {"kind": "checklist", "title": title, "intro": intro, "items": items, "never": never}


# ---- earn_links -----------------------------------------------------------------------------------------
def build_earn_links(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    if not ctx.pages:
        return []
    assets = linkable_assets(ctx)
    topic = topic_of(ctx, assets)
    name = brand(ctx)
    if assets:
        listed = ", ".join(a.title for a in assets[:3])
        pick = item(
            "pick-pages",
            "Choose the one to three pages you would be proud to be cited for.",
            f"Suggested from your own site and search data: {listed}. Pick pages that give something no one else does.",
        )
    else:
        pick = item(
            "pick-pages",
            "Create or choose one page worth citing: a guide, a tool, a data table or a clear explanation.",
            "No page on the site is long and substantial enough yet to be the obvious thing to link to.",
        )
    items = [
        pick,
        item(
            "unlinked-mentions",
            "Find places that already mention you without linking, and ask for the mention to become a link.",
            f'Search for "{name}" and "{ctx.domain}" with your own site excluded (add -site:{ctx.domain}). Write to the '
            "author or site owner, thank them, and give the exact page you would like linked.",
        ),
        item(
            "partners",
            "List the businesses and groups you already deal with, and ask each whether a partners, stockists, members or "
            "resources page could list you.",
            "Suppliers, manufacturers, trade associations, local chambers, events you sponsor, charities you support.",
        ),
        item(
            "resource-pages",
            "Find pages that collect resources on your topic and suggest your page only where it adds something they lack.",
            f'Search for "{topic} resources", "{topic} guide" and "useful links {topic}". Read the page first; never ask '
            "for a link to something that does not belong.",
        ),
        item(
            "expert-contributions",
            "Offer a quote, a correction or a short article, signed with a real name, to publications in your field.",
            "Local news, trade magazines and newsletters often want an expert voice. Keep the offer specific.",
        ),
        item(
            "broken-links",
            "Look for broken links on relevant sites that one of your pages could properly replace, and tell the owner.",
            "Be brief and helpful: name the broken URL, say where it is, and offer your page as a replacement only if it "
            "really covers the same ground.",
        ),
        item(
            "log-and-follow-up",
            "Keep a simple log: who you wrote to, when, and what happened. Follow up once after a week, then let it go.",
        ),
        item(
            "review-results",
            "Once a month, open Search Console > Links and note new linking sites; thank the ones that linked.",
        ),
    ]
    return [
        Candidate(
            type="earn_links",
            target_url=None,
            field="links",
            source_kind="site",
            sources=[],
            proposed_value=_checklist(
                "Earn links and mentions",
                "Links from other sites that are chosen by people, not arranged, are among the strongest signals a page "
                "can have. They take weeks, and they come from being genuinely worth citing.",
                items,
                NEVER_LINKS,
            ),
            evidence={
                "why": [
                    "Pages rank better when other sites choose to cite them, and this is work only a person can do."
                ],
                "assets": [a.as_dict() for a in assets],
                "topic": topic,
                "inlink_poor_assets": [a.url for a in assets if a.inlinks <= 1],
            },
        )
    ]


# ---- author_eeat ----------------------------------------------------------------------------------------
def build_author_eeat(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    pages = sorted({f.url for f in findings if f.url})
    if not pages:
        return []
    about = find_pages(ctx, ABOUT_PATH)
    contact = find_pages(ctx, CONTACT_PATH)
    items = [
        item(
            "byline-and-date",
            "Show a real author name and the publish date, and the last-updated date when it differs, near the top of "
            "each article listed.",
            f"{len(pages)} article page(s) show no author or no date.",
        ),
        item(
            "author-pages",
            "Create one page per author with a photo, a short true bio, what they know about the subject, and links to "
            "their other work. Link every byline to it.",
            "Existing page(s) that look like author or team pages: " + ", ".join(about[:3])
            if about
            else "No author, team or About page was found by the crawl.",
        ),
        item(
            "credentials",
            "Say what makes each author qualified to write on the subject: experience, training, or first-hand use.",
            "Only what is true and can be checked. A short honest line beats a long vague one.",
        ),
        item(
            "about-page",
            "Make sure an About page says who runs the site, since when, and why you are worth listening to."
            if about
            else "Add an About page that says who runs the site, since when, and why you are worth listening to.",
            ("Found: " + about[0]) if about else None,
        ),
        item(
            "contact",
            "Make it easy to reach a real person (an email address or a form, plus a postal address for a business)."
            if contact
            else "Add a contact page with an email address or form, and a postal address if you are a business.",
            ("Found: " + contact[0]) if contact else None,
        ),
        item(
            "sources",
            "Where an article states a fact, link to where it came from (a primary source where there is one).",
            "On health, money, legal and safety topics, have a qualified person review the page and name them.",
        ),
        item(
            "markup",
            "Add the author and date to each article's Article JSON-LD (author as a Person with name and url, "
            "datePublished, dateModified).",
            "Do this after the byline is visible on the page: markup should describe what readers can see. Search OS "
            "can propose the markup for you in the action plan once the byline is there.",
        ),
    ]
    return [
        Candidate(
            type="author_eeat",
            target_url=None,
            field="authors",
            source_kind="audit",
            sources=sorted({f.source for f in findings}),
            proposed_value=_checklist(
                "Show who is behind the content",
                "Readers, search engines and AI assistants all look for signs of who wrote something and why they can be "
                "trusted: experience, expertise, authority and trustworthiness (E-E-A-T). They have to be real.",
                items,
                NEVER_TRUST,
            ),
            evidence={
                "why": _why("AUTHOR_DATE_MISSING"),
                "count": len(pages),
                "urls": pages[:MAX_URLS_LISTED],
                "urls_truncated": len(pages) > MAX_URLS_LISTED,
                "about_pages": about[:5],
                "contact_pages": contact[:5],
            },
        )
    ]


# ---- collect_reviews ------------------------------------------------------------------------------------
LOCAL_TYPES = {
    "LocalBusiness",
    "Restaurant",
    "Store",
    "Dentist",
    "Physician",
    "LegalService",
    "RealEstateAgent",
    "ProfessionalService",
    "HomeAndConstructionBusiness",
    "AutoRepair",
    "HealthAndBeautyBusiness",
    "FoodEstablishment",
}


def build_collect_reviews(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    if not ctx.pages:
        return []
    types = schema_types(ctx)
    local = bool(types & LOCAL_TYPES)
    products = "Product" in types
    where: list[dict[str, str]] = []
    if local:
        where.append(
            item(
                "business-profile",
                "Claim and complete your Google Business Profile (name, address, hours, categories, photos).",
                "The site's markup describes a local business, so this is where most reviews will count.",
            )
        )
    if products:
        where.append(
            item(
                "product-reviews",
                "Let verified buyers review products on the product page, and show those reviews on the page.",
                "Product markup was found on the site. Mark reviews up only when they are visible on the page and written "
                "by customers; Search OS never adds rating markup it cannot verify from the page.",
            )
        )
    if not where:
        where.append(
            item(
                "pick-platform",
                "Pick where your customers already leave reviews: a profile on a review site in your field, Google, or "
                "testimonials with permission and a name.",
                "Choose one place to start; a hundred reviews in one place help more than ten in ten.",
            )
        )
    items = [
        *where,
        item(
            "ask-everyone",
            "Ask every customer, at the right moment (after delivery, or after a problem is solved), with a short direct "
            "link to the review page.",
            "Send the same request to everyone, not only the customers you expect to be happy.",
        ),
        item(
            "make-it-easy",
            "Put the review link in the order confirmation or follow-up email and in your email signature.",
        ),
        item(
            "reply",
            "Reply to every review, briefly and politely, including the critical ones. Say what you will do about a "
            "problem.",
        ),
        item(
            "learn",
            "Once a month, read the new reviews together and write down the one thing customers praise and the one they "
            "dislike. Use the praise on your pages.",
        ),
        item(
            "track",
            "Record the number of reviews and the average rating each month, so you can see the trend.",
        ),
    ]
    return [
        Candidate(
            type="collect_reviews",
            target_url=None,
            field="reviews",
            source_kind="site",
            sources=[],
            proposed_value=_checklist(
                "Collect genuine reviews",
                "Real customer reviews build trust with visitors and are a signal for local and product searches. They "
                "have to be genuine, and they have to come from you asking.",
                items,
                NEVER_REVIEWS,
            ),
            evidence={
                "why": ["Reviews are written by customers, so only a person can ask for them."],
                "local_business_markup": local,
                "product_markup": products,
                "schema_types": sorted(types)[:20],
            },
        )
    ]


# ---- digital_pr -----------------------------------------------------------------------------------------
def build_digital_pr(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    if not ctx.pages:
        return []
    assets = linkable_assets(ctx)
    queries = top_queries(ctx)
    topic = topic_of(ctx, assets)
    if queries:
        ideas = ", ".join(f'"{q["query"]}"' for q in queries[:3])
        topic_detail = f"People already find you for {ideas}. A study, a count or a test on one of these is a natural fit."
    elif assets:
        topic_detail = f"Your most substantial page is about {assets[0].title}; start there."
    else:
        topic_detail = (
            "Think of a question your customers ask often that you can answer with your own numbers."
        )
    items = [
        item(
            "topic",
            f"Pick one topic related to {topic} where you hold first-hand information others do not.",
            topic_detail
            + " Good raw material: your own figures, a short customer survey, a test you ran, a local angle.",
        ),
        item(
            "publish",
            "Publish it as a single page on your site: the main finding in the first paragraph, tables or charts others may "
            "reuse, how you got the numbers, the date and the author.",
        ),
        item(
            "list",
            "Make a short list of 10 to 20 people who cover this topic: journalists, trade publications, newsletter "
            "writers, podcasters. Read their recent work first.",
        ),
        item(
            "pitch",
            "Write each pitch by hand: the finding in two sentences, why their readers will care, the link, and an offer "
            "to answer questions.",
            "Never attach files to a first message. Never send the same text to everyone.",
        ),
        item("follow-up", "Follow up once after about a week. If there is no reply, move on."),
        item(
            "measure",
            "Measure in Search Console (Links, and brand searches) and in your visitor statistics (referrals) a month and "
            "three months later.",
        ),
    ]
    return [
        Candidate(
            type="digital_pr",
            target_url=None,
            field="pr",
            source_kind="site",
            sources=[],
            proposed_value=_checklist(
                "Publish something worth citing and pitch it",
                "Digital PR means giving writers a reason to mention you: something new, true and useful. It is slow, "
                "personal work that cannot be automated.",
                items,
                NEVER_PR,
            ),
            evidence={
                "why": [
                    "Coverage and links come from a story people want to tell, and only a person can pitch one."
                ],
                "topic": topic,
                "queries": queries,
                "assets": [a.as_dict() for a in assets[:3]],
            },
        )
    ]


MANUAL_BUILDERS = {
    "earn_links": build_earn_links,
    "author_eeat": build_author_eeat,
    "collect_reviews": build_collect_reviews,
    "digital_pr": build_digital_pr,
}
