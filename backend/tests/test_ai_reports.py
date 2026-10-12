"""AI-report CSV importers: header mapping, number/date parsing, clear errors, storage and the API."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.integrations.ai_reports import CsvFormatError, parse_date, parse_number, parse_report
from searchos.models import AiReportImport, AiReportRow
from tests.conftest import auth, register

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "ai_reports"


def csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode()


# ---- parsing ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [("1,234", 1234.0), ("12.5%", 0.125), ("  7 ", 7.0), ("-", None), ("", None), ("0", 0.0), ("-3.5", -3.5)],
)
def test_parse_number(text: str, expected: float | None) -> None:
    assert parse_number(text) == expected


def test_parse_number_rejects_text() -> None:
    with pytest.raises(ValueError, match="abc"):
        parse_number("abc")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-01", date(2026, 9, 1)),
        ("2026/09/01", date(2026, 9, 1)),
        ("Sep 1, 2026", date(2026, 9, 1)),
        ("1 September 2026", date(2026, 9, 1)),
        ("20260901", date(2026, 9, 1)),
        ("9/1/2026", date(2026, 9, 1)),  # month first unless impossible
        ("25/12/2026", date(2026, 12, 25)),
    ],
)
def test_parse_date(text: str, expected: date) -> None:
    assert parse_date(text) == expected


def test_parse_date_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="soon"):
        parse_date("soon")


def test_header_aliases_are_case_and_spelling_insensitive() -> None:
    parsed = parse_report(
        csv("DAY,AI Impressions,Clicks", "2026-09-01,100,3", '2026-09-02,"1,200",4'), "gsc_genai"
    )
    assert parsed.shape == "date" and parsed.metrics == ["clicks", "impressions"]
    assert parsed.mapping == {
        "DAY": "date",
        "AI Impressions": "metric:impressions",
        "Clicks": "metric:clicks",
    }
    assert (parsed.date_min, parsed.date_max) == (date(2026, 9, 1), date(2026, 9, 2))
    values = {(r.date, r.metric): r.value for r in parsed.rows}
    assert values[(date(2026, 9, 2), "impressions")] == 1200.0 and len(parsed.rows) == 4


def test_page_and_query_columns_and_semicolon_delimiter_and_bom() -> None:
    data = "﻿Grounding Query;Cited URL;Citations\nbest widgets;https://example.com/a;5\nwidget care;https://example.com/b;2\n"
    parsed = parse_report(data.encode("utf-8"), "bing_ai")
    assert parsed.shape == "page+query"
    assert {(r.query, r.page, r.value) for r in parsed.rows} == {
        ("best widgets", "https://example.com/a", 5.0),
        ("widget care", "https://example.com/b", 2.0),
    }


def test_utf16_exports_from_excel_are_read() -> None:
    data = "Date\tCitations\n2026-09-01\t5\n".encode("utf-16")
    parsed = parse_report(data, "bing_ai")
    assert parsed.rows[0].metric == "citations" and parsed.rows[0].value == 5.0


def test_cited_pages_is_a_page_when_values_are_urls_and_a_count_when_numeric() -> None:
    urls = parse_report(csv("Cited pages,Citations", "https://example.com/a,5"), "bing_ai")
    assert urls.shape == "page" and urls.mapping["Cited pages"] == "page"
    counts = parse_report(csv("Date,Citations,Cited pages", "2026-09-01,20,5"), "bing_ai")
    assert counts.shape == "date" and counts.mapping["Cited pages"] == "metric:cited_pages"
    assert {r.metric: r.value for r in counts.rows} == {"citations": 20.0, "cited_pages": 5.0}


def test_unknown_numeric_columns_are_kept_under_their_own_name() -> None:
    parsed = parse_report(csv("Date,Impressions,Average Position", "2026-09-01,10,3.4"), "gsc_genai")
    assert parsed.extra_metrics == ["average_position"] and "average_position" in parsed.metrics


def test_percentages_become_fractions() -> None:
    parsed = parse_report(csv("Date,Impressions,CTR", "2026-09-01,100,2.5%"), "gsc_genai")
    assert {r.metric: r.value for r in parsed.rows}["ctr"] == 0.025


def test_bad_rows_are_skipped_with_a_warning_but_good_rows_import() -> None:
    parsed = parse_report(
        csv("Date,Impressions", "2026-09-01,100", "not a date,5", "2026-09-03,abc", "2026-09-04,7"),
        "gsc_genai",
    )
    assert len(parsed.rows) == 2 and parsed.skipped_rows == 2
    assert any("skipped" in w for w in parsed.warnings)


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        (b"", "empty"),
        (csv("Date,Impressions"), "no data rows"),
        (csv("Foo,Bar", "1,2"), "Columns found: Foo, Bar"),
        (csv("Date,Notes", "2026-09-01,hello"), "metric column"),
        (csv("Impressions,Clicks", "5,6"), "Date, Page or Query"),
        (csv("Date,Date,Impressions", "2026-09-01,2026-09-01,5"), "More than one column"),
        (csv("Date,Impressions", "x,y"), "No data rows"),
    ],
)
def test_clear_errors_list_the_columns_found(data: bytes, needle: str) -> None:
    with pytest.raises(CsvFormatError) as info:
        parse_report(data, "gsc_genai")
    assert needle in str(info.value)


def test_error_message_names_the_report_type() -> None:
    with pytest.raises(CsvFormatError, match="Bing AI Performance"):
        parse_report(csv("Foo,Bar", "1,2"), "bing_ai")
    with pytest.raises(CsvFormatError, match="Search Console Generative AI"):
        parse_report(csv("Foo,Bar", "1,2"), "gsc_genai")


def test_oversized_file_is_refused_politely() -> None:
    with pytest.raises(CsvFormatError, match="larger than"):
        parse_report(b"x" * (9 * 1024 * 1024), "gsc_genai")


# ---- the synthetic fixtures -------------------------------------------------------------------------
def test_synthetic_fixture_files_parse() -> None:
    daily = parse_report((FIXTURES / "synthetic_gsc_genai.csv").read_bytes(), "gsc_genai")
    assert daily.shape == "date" and len({r.date for r in daily.rows}) == 30
    pages = parse_report((FIXTURES / "synthetic_gsc_genai_pages.csv").read_bytes(), "gsc_genai")
    assert pages.shape == "page" and max(r.value for r in pages.rows) == 5200.0
    bing = parse_report((FIXTURES / "synthetic_bing_ai_performance.csv").read_bytes(), "bing_ai")
    assert bing.shape == "date" and {"citations", "cited_pages"} <= set(bing.metrics)
    grounding = parse_report((FIXTURES / "synthetic_bing_ai_grounding_queries.csv").read_bytes(), "bing_ai")
    assert grounding.shape == "query"
    cited = parse_report((FIXTURES / "synthetic_bing_ai_cited_pages.csv").read_bytes(), "bing_ai")
    assert cited.shape == "page"


# ---- API --------------------------------------------------------------------------------------------
async def upload(
    client: AsyncClient, tokens: dict, site_id: str, source: str, name: str, data: bytes | None = None
):  # type: ignore[no-untyped-def,type-arg]
    content = data if data is not None else (FIXTURES / name).read_bytes()
    return await client.post(
        f"/sites/{site_id}/ai-reports/{source}/import",
        files={"file": (name, content, "text/csv")},
        headers=auth(tokens),
    )


async def make_site(client: AsyncClient, tokens: dict) -> str:  # type: ignore[type-arg]
    return str(
        (await client.post("/sites", json={"domain": "example.com"}, headers=auth(tokens))).json()["id"]
    )


async def test_upload_chart_data_and_tables(client: AsyncClient) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    empty = (await client.get(f"/sites/{site_id}/ai-reports/gsc_genai", headers=auth(tokens))).json()
    assert empty["imports"] == [] and empty["series"] == [] and empty["primary_metric"] is None

    r1 = await upload(client, tokens, site_id, "gsc_genai", "synthetic_gsc_genai.csv")
    r2 = await upload(client, tokens, site_id, "gsc_genai", "synthetic_gsc_genai_pages.csv")
    assert r1.status_code == r2.status_code == 201, r1.text
    assert (
        r1.json()["row_count"] == 90
        and r1.json()["shape"] == "date"
        and "impressions" in r1.json()["metrics"]
    )
    report = (await client.get(f"/sites/{site_id}/ai-reports/gsc_genai", headers=auth(tokens))).json()
    assert report["primary_metric"] == "impressions" and report["series_shape"] == "date"
    assert len(report["series"]) == 30 and "impressions" in report["series"][0]["values"]
    assert report["top_pages"][0]["key"] == "https://example.com/blog/how-to-choose-widgets"
    assert report["top_pages"][0]["values"]["impressions"] == 5200.0
    assert len(report["imports"]) == 2

    # The other source is separate.
    other = (await client.get(f"/sites/{site_id}/ai-reports/bing_ai", headers=auth(tokens))).json()
    assert other["imports"] == []


async def test_bing_report_uses_citations_as_primary_metric(client: AsyncClient) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    for name in (
        "synthetic_bing_ai_performance.csv",
        "synthetic_bing_ai_grounding_queries.csv",
        "synthetic_bing_ai_cited_pages.csv",
    ):
        assert (await upload(client, tokens, site_id, "bing_ai", name)).status_code == 201
    report = (await client.get(f"/sites/{site_id}/ai-reports/bing_ai", headers=auth(tokens))).json()
    assert report["primary_metric"] == "citations"
    assert report["top_queries"][0] == {
        "key": "best widgets for small gardens",
        "values": {"citations": 44.0},
    }
    assert report["top_pages"][0]["key"].endswith("/blog/how-to-choose-widgets")


async def test_reuploading_an_overlapping_export_replaces_instead_of_double_counting(
    client: AsyncClient, session: AsyncSession
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    await upload(
        client, tokens, site_id, "bing_ai", "a.csv", csv("Date,Citations", "2026-09-01,5", "2026-09-02,6")
    )
    second = await upload(
        client, tokens, site_id, "bing_ai", "b.csv", csv("Date,Citations", "2026-09-02,10", "2026-09-03,7")
    )
    assert second.json()["replaced_rows"] == 1
    report = (await client.get(f"/sites/{site_id}/ai-reports/bing_ai", headers=auth(tokens))).json()
    assert {p["date"]: p["values"]["citations"] for p in report["series"]} == {
        "2026-09-01": 5.0,
        "2026-09-02": 10.0,  # the newer file wins for the overlapping day
        "2026-09-03": 7.0,
    }
    # Uploading a file that covers the first completely removes the now-empty import.
    await upload(
        client, tokens, site_id, "bing_ai", "c.csv", csv("Date,Citations", "2026-08-30,1", "2026-09-04,2")
    )
    assert (await session.scalar(sa.select(sa.func.count()).select_from(AiReportImport))) == 1


async def test_bad_upload_returns_422_with_the_columns_found(client: AsyncClient) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    resp = await upload(client, tokens, site_id, "gsc_genai", "x.csv", csv("Foo,Bar", "1,2"))
    assert resp.status_code == 422
    assert "Columns found: Foo, Bar" in resp.json()["detail"]
    assert (
        await upload(client, tokens, site_id, "nonsense", "x.csv", csv("Date,Clicks", "2026-09-01,1"))
    ).status_code == 404


async def test_delete_import_and_permissions(client: AsyncClient, session: AsyncSession) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    done = await upload(client, tokens, site_id, "bing_ai", "a.csv", csv("Date,Citations", "2026-09-01,5"))
    import_id = done.json()["id"]
    stranger = await register(client, "s@example.com", org_name="S")
    assert (
        await upload(client, stranger, site_id, "bing_ai", "a.csv", csv("Date,Citations", "2026-09-01,5"))
    ).status_code == 404
    assert (
        await client.get(f"/sites/{site_id}/ai-reports/bing_ai", headers=auth(stranger))
    ).status_code == 404
    assert (
        await client.delete(f"/sites/{site_id}/ai-reports/imports/{import_id}", headers=auth(stranger))
    ).status_code == 404
    assert (
        await client.delete(f"/sites/{site_id}/ai-reports/imports/{import_id}", headers=auth(tokens))
    ).status_code == 204
    assert (await session.scalar(sa.select(sa.func.count()).select_from(AiReportRow))) == 0
