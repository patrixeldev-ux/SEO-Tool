"""CSV importers for the two first-party AI reports: Search Console's "Generative AI" report and Bing Webmaster
Tools' "AI Performance" report.

The exact column names of these exports are not documented, so this parser does not assume them. It maps headers
by case-insensitive aliases (see ALIASES), recognises numbers like "1,234" and "12.5%", and when it cannot find
what it needs it raises `CsvFormatError` with the columns it *did* find, so the owner can see what to rename.

Output is "long" rows: one per (date, page, query, metric). Unknown numeric columns are kept under their own
snake_case name rather than dropped; the summary lists them.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

Source = Literal["gsc_genai", "bing_ai"]
SOURCES: tuple[Source, ...] = ("gsc_genai", "bing_ai")
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100_000

# Canonical column role -> accepted header spellings (compared after lower-casing and squashing spaces/_/-).
ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "day", "dates", "report date"),
    "page": (
        "page", "pages", "url", "urls", "page url", "cited url", "cited urls", "cited page url",
        "top pages", "top cited pages", "landing page",
    ),
    "query": (
        "query", "queries", "search query", "grounding query", "grounding queries", "top queries",
        "grounding queries (sample)", "prompt", "prompts",
    ),
}  # fmt: skip
METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "impressions": ("impressions", "ai impressions", "generative ai impressions", "gen ai impressions"),
    "clicks": ("clicks", "ai clicks", "generative ai clicks"),
    "citations": ("citations", "total citations", "ai citations", "citation count", "cited count"),
    "cited_pages": ("cited pages", "cited page count", "average cited pages", "avg cited pages", "pages cited"),
    "grounding_queries": ("grounding queries count", "grounding query count", "number of grounding queries"),
    "ctr": ("ctr", "click through rate", "click-through rate"),
}  # fmt: skip
# "Cited pages" can be a list of URLs or a count: decided from the values.
AMBIGUOUS_PAGE_OR_COUNT = frozenset({"cited pages", "cited page", "grounding queries", "grounding query"})

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y%m%d")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


class CsvFormatError(ValueError):
    """The file could not be understood. `str(error)` is a complete message for the owner."""

    def __init__(self, message: str, columns: list[str] | None = None) -> None:
        super().__init__(message)
        self.columns = columns or []


@dataclass(frozen=True, slots=True)
class ParsedRow:
    date: date | None
    page: str | None
    query: str | None
    metric: str
    value: float


@dataclass(slots=True)
class ParsedReport:
    source: Source
    rows: list[ParsedRow]
    columns: list[str]
    mapping: dict[str, str]  # header as written -> role ("date", "page", "query", "metric:citations", ...)
    shape: str  # which dimensions are present: "date", "date+page", "page+query", ...
    metrics: list[str]
    extra_metrics: list[str]
    date_min: date | None
    date_max: date | None
    skipped_rows: int = 0
    warnings: list[str] = field(default_factory=list)


def _norm(header: str) -> str:
    return " ".join(re.sub(r"[_\-]+", " ", header.strip().lower()).split())


def _snake(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_") or "value"


def _decode(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise CsvFormatError(
            f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Export a shorter date range."
        )
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_number(text: str) -> float | None:
    """'1,234' -> 1234.0; '12.5%' -> 0.125; '' / '-' -> None; None when it is not a number."""
    value = text.strip().replace(chr(0xA0), "").replace(" ", "")
    if value in ("", "-", chr(0x2014), "n/a", "N/A"):
        return None
    percent = value.endswith("%")
    value = value.rstrip("%").replace(",", "")
    if not _NUMBER.match(value):
        raise ValueError(text)
    number = float(value)
    return number / 100 if percent else number


def parse_date(text: str) -> date:
    value = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if match:
        a, b, year = (int(g) for g in match.groups())
        month, day = (b, a) if a > 12 else (a, b)  # d/m/Y only when the first number cannot be a month
        try:
            return date(year, month, day)
        except ValueError:
            pass
    raise ValueError(text)


def _looks_like_url(value: str) -> bool:
    v = value.strip().lower()
    return v.startswith(("http://", "https://", "/")) or ("." in v and " " not in v and "/" in v)


def _sniff(sample: str) -> type[csv.Dialect]:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def parse_report(data: bytes, source: Source) -> ParsedReport:
    if source not in SOURCES:
        raise CsvFormatError(f"Unknown report type {source!r}.")
    text = _decode(data)
    if not text.strip():
        raise CsvFormatError("The file is empty.")
    reader = csv.reader(io.StringIO(text), _sniff(text[:4096]))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise CsvFormatError("The file is empty.") from None
    table = [row for row in reader if any(cell.strip() for cell in row)]
    if len(table) > MAX_ROWS:
        raise CsvFormatError(f"The file has more than {MAX_ROWS:,} rows. Export a shorter date range.")
    if not table:
        raise CsvFormatError("The file has a header row but no data rows.", header)

    roles: dict[int, str] = {}  # column index -> role
    for index, name in enumerate(header):
        key = _norm(name)
        if not key:
            continue
        values = [row[index] for row in table[:50] if index < len(row) and row[index].strip()]
        if key in AMBIGUOUS_PAGE_OR_COUNT:
            urls = bool(values) and all(_looks_like_url(v) for v in values)
            words = bool(values) and not all(_is_number(v) for v in values)
            if "page" in key:
                roles[index] = (
                    "page"
                    if urls or (words and not all(_is_number(v) for v in values))
                    else "metric:cited_pages"
                )
            else:
                roles[index] = "query" if words else "metric:grounding_queries"
            continue
        for role, aliases in ALIASES.items():
            if key in aliases:
                roles[index] = role
                break
        else:
            for metric, aliases in METRIC_ALIASES.items():
                if key in aliases:
                    roles[index] = f"metric:{metric}"
                    break

    duplicates = [r for r in ("date", "page", "query") if sum(1 for v in roles.values() if v == r) > 1]
    if duplicates:
        raise CsvFormatError(
            f"More than one column looks like the {duplicates[0]} column. Columns found: {', '.join(header)}.",
            header,
        )
    dims = [r for r in ("date", "page", "query") if r in roles.values()]
    known_metric_cols = [i for i, r in roles.items() if r.startswith("metric:")]

    # Numeric columns we do not recognise are kept under their own name.
    extra_cols: list[int] = []
    for index, name in enumerate(header):
        if index in roles or not name.strip():
            continue
        cells = [row[index] for row in table if index < len(row) and row[index].strip()]
        if cells and all(_is_number(c) for c in cells):
            roles[index] = f"metric:{_snake(name)}"
            extra_cols.append(index)

    metric_cols = [i for i, r in roles.items() if r.startswith("metric:")]
    if not known_metric_cols or not dims:
        missing = []
        if not dims:
            missing.append("a Date, Page or Query column")
        if not known_metric_cols:
            missing.append("a metric column such as Impressions, Clicks or Citations")
        raise CsvFormatError(
            f"This doesn't look like a {_label(source)} export: it is missing {' and '.join(missing)}. "
            f"Columns found: {', '.join(header)}.",
            header,
        )

    rows: list[ParsedRow] = []
    skipped = 0
    warnings: list[str] = []
    date_index = next((i for i, r in roles.items() if r == "date"), None)
    page_index = next((i for i, r in roles.items() if r == "page"), None)
    query_index = next((i for i, r in roles.items() if r == "query"), None)
    for line_no, row in enumerate(table, start=2):
        try:
            when = parse_date(row[date_index]) if date_index is not None and row[date_index].strip() else None
            if date_index is not None and when is None:
                raise ValueError("no date")
            page = row[page_index].strip() if page_index is not None and page_index < len(row) else None
            query = row[query_index].strip() if query_index is not None and query_index < len(row) else None
            metric_values = [
                (roles[i][len("metric:") :], parse_number(row[i]) if i < len(row) else None)
                for i in metric_cols
            ]
        except (ValueError, IndexError):
            skipped += 1
            if len(warnings) < 5:
                warnings.append(f"Line {line_no} was skipped because a date or number could not be read.")
            continue
        added = False
        for metric, number in metric_values:
            if number is not None:
                rows.append(ParsedRow(when, page or None, query or None, metric, number))
                added = True
        if not added:
            skipped += 1
    if not rows:
        raise CsvFormatError("No data rows could be read from the file.", header)
    if skipped:
        warnings.append(f"{skipped} row(s) were skipped.")

    dates = [r.date for r in rows if r.date is not None]
    metrics = sorted({r.metric for r in rows})
    extra = sorted(roles[i][len("metric:") :] for i in extra_cols)
    return ParsedReport(
        source=source,
        rows=rows,
        columns=header,
        mapping={header[i]: roles[i] for i in sorted(roles)},
        shape="+".join(dims),
        metrics=metrics,
        extra_metrics=extra,
        date_min=min(dates) if dates else None,
        date_max=max(dates) if dates else None,
        skipped_rows=skipped,
        warnings=warnings,
    )


def _is_number(text: str) -> bool:
    try:
        return parse_number(text) is not None or text.strip() in ("", "-")
    except ValueError:
        return False


def _label(source: Source) -> str:
    return "Search Console Generative AI" if source == "gsc_genai" else "Bing AI Performance"
