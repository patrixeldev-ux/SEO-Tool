"""Development helper: fill a demo site with realistic SYNTHETIC Search Console data (no Google account needed).

    python -m searchos.dev_seed_gsc --email demo@example.com

Creates (or reuses) the site "harborandpine.example" in the user's first organisation, replaces any Search
Console data it holds with 16 months of made-up daily numbers, and recomputes the opportunities. The data is
built so each finder has something to find: queries a few places below the top 3, a page whose click-through is
far under the curve, a page that lost clicks recently, two pages competing for one query, plus "conversational"
queries ("yes", "tell me more") and branded queries. Totals are about 12% above the sum of the query rows, like
real Search Console, whose totals include anonymised queries.

Refuses to run when ENVIRONMENT=production. Nothing here talks to Google.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from searchos.core.auth import normalize_email
from searchos.core.config import get_settings
from searchos.core.db import dispose_engine, get_sessionmaker
from searchos.integrations.gsc_dates import earliest_day, latest_day
from searchos.models import GscDailyTotal, GscRow, Membership, Opportunity, SearchInsights, Site, User
from searchos.search.insights import recompute_insights
from searchos.stats.conversational import is_conversational

DEMO_DOMAIN = "harborandpine.example"
DEMO_NAME = "Harbor & Pine Coffee (demo search data)"
ANONYMISED_SHARE = 0.12  # totals exceed the query rows by this much

# Click-through by rounded position for a healthy result; the made-up data follows it with noise.
NATURAL_CTR = {1: 0.28, 2: 0.15, 3: 0.10, 4: 0.07, 5: 0.05, 6: 0.04, 7: 0.03, 8: 0.025, 9: 0.02, 10: 0.017}
TAIL_CTR = 0.01


def natural_ctr(position: float) -> float:
    return NATURAL_CTR.get(int(position + 0.5), TAIL_CTR)


@dataclass(frozen=True)
class Series:
    page: str
    query: str
    position: float
    daily_impressions: int
    ctr_factor: float = 1.0  # 1.0 = as healthy as the curve; < 1 = under-performing
    recent_factor: float = 1.0  # multiplies clicks in the newest 28 days (a page that is losing clicks)


BASE = f"https://{DEMO_DOMAIN}"
SERIES: list[Series] = [
    # Branded: the home page at the top.
    Series(f"{BASE}/", "harbor and pine coffee", 1.1, 90, 2.2),
    Series(f"{BASE}/", "harbor pine coffee roasters", 1.3, 40, 2.0),
    # Healthy pages that give the curve its shape.
    Series(f"{BASE}/shop/espresso-beans", "espresso beans", 2.1, 240),
    Series(f"{BASE}/shop/espresso-beans", "buy espresso beans online", 3.2, 120),
    Series(f"{BASE}/shop/decaf", "decaf coffee beans", 3.8, 150),
    Series(f"{BASE}/shop/decaf", "swiss water decaf", 4.1, 90),
    Series(f"{BASE}/shop/subscriptions", "coffee subscription", 5.2, 220),
    Series(f"{BASE}/shop/subscriptions", "monthly coffee delivery", 6.0, 160),
    Series(f"{BASE}/blog/cold-brew-guide", "cold brew ratio", 2.8, 180),
    Series(f"{BASE}/blog/cold-brew-guide", "how to make cold brew", 4.4, 260),
    Series(f"{BASE}/blog/pour-over", "pour over coffee", 7.0, 200),
    Series(f"{BASE}/blog/pour-over", "v60 recipe", 3.1, 110),
    Series(f"{BASE}/blog/roast-levels", "light vs dark roast", 5.1, 130),
    Series(f"{BASE}/blog/roast-levels", "medium roast meaning", 8.2, 70),
    Series(f"{BASE}/about", "coffee roaster near me", 9.3, 60),
    # STRIKING_DISTANCE: lots of impressions a few places below the top 3.
    Series(f"{BASE}/blog/best-coffee-grinders", "best coffee grinder", 6.2, 900),
    Series(f"{BASE}/blog/aeropress-recipes", "aeropress recipe", 8.4, 700),
    Series(f"{BASE}/blog/coffee-storage", "how to store coffee beans", 11.5, 640),
    Series(f"{BASE}/shop/gift-boxes", "coffee gift set", 13.0, 520),
    # CTR_GAP: well placed but nobody clicks (weak title or description).
    Series(f"{BASE}/blog/french-press", "french press ratio", 3.0, 480, 0.18),
    Series(f"{BASE}/blog/coffee-grind-chart", "coffee grind size chart", 4.0, 420, 0.22),
    # CANNIBALISATION: two pages for one query.
    Series(f"{BASE}/shop/grinders", "best coffee grinder", 8.0, 500, 0.8),
    Series(f"{BASE}/blog/home-espresso", "espresso at home", 7.1, 260),
    Series(f"{BASE}/shop/espresso-machines", "espresso at home", 7.6, 240),
    # DECAY: clicks fell by more than half in the last 28 days.
    Series(f"{BASE}/guides/espresso-basics", "how to pull espresso shot", 4.2, 520, 1.0, 0.4),
    Series(f"{BASE}/guides/espresso-basics", "espresso shot time", 5.0, 300, 1.0, 0.4),
]

# Searches that are really follow-ups inside an AI conversation. Many impressions, almost no clicks.
CONVERSATIONAL = [
    ("yes", 5.0, 160),
    ("tell me more", 6.0, 90),
    ("what about the second one", 7.0, 60),
    ("how about cheaper", 5.5, 45),
]


def generate(
    today: date, seed: int = 7
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:  # (query rows, daily totals) without site_id
    rng = random.Random(seed)  # noqa: S311 (made-up demo data, not security)
    end = latest_day(today)
    start = earliest_day(today) + timedelta(days=1)
    rows: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    conv_pages = [f"{BASE}/blog/cold-brew-guide", f"{BASE}/blog/pour-over", f"{BASE}/shop/subscriptions"]
    day = start
    while day <= end:
        recent = (end - day).days < 28
        weekday = 0.72 if day.weekday() >= 5 else 1.0
        growth = 0.85 + 0.3 * (day - start).days / max((end - start).days, 1)  # slow organic growth
        day_impr = day_clicks = 0
        pos_weighted = 0.0
        specs: list[tuple[str, str, float, int, float]] = [
            (
                s.page,
                s.query,
                s.position,
                s.daily_impressions,
                s.ctr_factor * (s.recent_factor if recent else 1.0),
            )
            for s in SERIES
        ]
        for i, (query, position, impr) in enumerate(CONVERSATIONAL):
            specs.append((conv_pages[i % len(conv_pages)], query, position, impr, 0.05))
        for page, query, position, base, factor in specs:
            impressions = max(0, round(base * weekday * growth * rng.uniform(0.8, 1.2)))
            if impressions == 0:
                continue
            ctr = min(0.9, natural_ctr(position) * factor * rng.uniform(0.85, 1.15))
            clicks = min(impressions, round(impressions * ctr))
            pos = max(1.0, position + rng.uniform(-0.4, 0.4))
            rows.append(
                {
                    "date": day,
                    "page": page,
                    "query": query,
                    "country": "usa",
                    "device": "MOBILE" if rng.random() < 0.6 else "DESKTOP",
                    "clicks": clicks,
                    "impressions": impressions,
                    "ctr": clicks / impressions,
                    "position": round(pos, 2),
                    "is_conversational": is_conversational(query),
                }
            )
            day_impr += impressions
            day_clicks += clicks
            pos_weighted += pos * impressions
        extra_impr = round(day_impr * ANONYMISED_SHARE)
        extra_clicks = round(day_clicks * ANONYMISED_SHARE)
        total_impr = day_impr + extra_impr
        totals.append(
            {
                "date": day,
                "clicks": day_clicks + extra_clicks,
                "impressions": total_impr,
                "ctr": (day_clicks + extra_clicks) / total_impr if total_impr else 0.0,
                "position": round(pos_weighted / day_impr, 2) if day_impr else None,
            }
        )
        day += timedelta(days=1)
    return rows, totals


async def seed(email: str, today: date | None = None) -> str:
    today = today or datetime.now(UTC).date()
    async with get_sessionmaker()() as session:
        user = await session.scalar(sa.select(User).where(User.email == normalize_email(email)))
        if user is None:
            return f"No user {email}. Register in the app first (or run: python -m searchos.seed_demo)."
        membership = await session.scalar(
            sa.select(Membership)
            .where(Membership.user_id == user.id)
            .order_by(Membership.created_at, Membership.id)
            .limit(1)
        )
        if membership is None:
            return "This user does not belong to an organisation."
        site = await session.scalar(
            sa.select(Site).where(Site.org_id == membership.org_id, Site.domain == DEMO_DOMAIN)
        )
        if site is None:
            site = Site(org_id=membership.org_id, domain=DEMO_DOMAIN, display_name=DEMO_NAME)
            session.add(site)
            await session.flush()
        site_id = site.id
        for table in (GscRow, GscDailyTotal, Opportunity, SearchInsights):
            await session.execute(sa.delete(table).where(table.site_id == site_id))
        rows, totals = generate(today)
        for start in range(0, len(rows), 2000):
            await session.execute(
                sa.insert(GscRow), [{"site_id": site_id, **r} for r in rows[start : start + 2000]]
            )
        await session.execute(sa.insert(GscDailyTotal), [{"site_id": site_id, **t} for t in totals])
        await session.commit()
    summary = await recompute_insights(get_sessionmaker(), site_id, today=today)
    counts = (summary or {}).get("counts", {})
    return (
        f"Seeded {len(totals)} days / {len(rows):,} rows for {DEMO_DOMAIN} (site {site_id}).\n"
        f"Opportunities: {counts}\nOpen Search Console in the web app and pick '{DEMO_NAME}'."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--email", default="demo@example.com")
    args = parser.parse_args()
    if get_settings().is_production:
        print("Refusing to run when ENVIRONMENT=production.", file=sys.stderr)
        return 1

    async def go() -> int:
        try:
            message = await seed(args.email)
        finally:
            await dispose_engine()
        print(message)
        return 0 if message.startswith("Seeded") else 1

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
