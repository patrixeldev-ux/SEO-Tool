"""The AI crawlers we check robots.txt for. The data lives in `data/ai_bots.json`; every entry records the
vendor documentation it was verified against and when."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

Purpose = Literal["search", "user_fetch", "training"]
PURPOSES: tuple[Purpose, ...] = ("search", "user_fetch", "training")
BOTS_PATH = Path(__file__).parent / "data" / "ai_bots.json"


@dataclass(frozen=True, slots=True)
class AiBot:
    name: str  # the robots.txt user-agent token
    vendor: str
    purpose: Purpose
    source_url: str
    verified_on: str | None
    notes: str = ""


class BotDataError(ValueError):
    pass


def parse_bots(text: str) -> list[AiBot]:
    raw = json.loads(text)
    entries = raw["bots"] if isinstance(raw, dict) else raw
    bots: list[AiBot] = []
    seen: set[str] = set()
    for entry in entries:
        purpose = entry.get("purpose")
        if purpose not in PURPOSES:
            raise BotDataError(f"{entry.get('name')}: unknown purpose {purpose!r}")
        for key in ("name", "vendor", "source_url"):
            if not entry.get(key):
                raise BotDataError(f"{entry.get('name', '?')}: missing {key}")
        if entry["name"].lower() in seen:
            raise BotDataError(f"duplicate bot {entry['name']}")
        seen.add(entry["name"].lower())
        bots.append(
            AiBot(
                name=str(entry["name"]),
                vendor=str(entry["vendor"]),
                purpose=purpose,
                source_url=str(entry["source_url"]),
                verified_on=entry.get("verified_on"),
                notes=str(entry.get("notes", "")),
            )
        )
    return bots


@lru_cache
def load_ai_bots() -> tuple[AiBot, ...]:
    return tuple(parse_bots(BOTS_PATH.read_text(encoding="utf-8")))
