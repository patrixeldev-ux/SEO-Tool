"""Upload and read the first-party AI reports (Search Console "Generative AI", Bing "AI Performance")."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.deps import OrgContext, require_role
from searchos.integrations.ai_reports import (
    MAX_BYTES,
    SOURCES,
    CsvFormatError,
    ParsedReport,
    Source,
    parse_report,
)
from searchos.models import AiReportImport, AiReportRow, Role, Site

router = APIRouter(prefix="/sites/{site_id}/ai-reports", tags=["ai-reports"])
ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]
WriteCtx = Annotated[OrgContext, Depends(require_role(Role.member))]

# Metrics where adding rows together makes sense; anything else is only shown for date-only files.
ADDITIVE = frozenset({"impressions", "clicks", "citations", "grounding_queries"})
PRIMARY_ORDER = ("citations", "impressions", "clicks", "cited_pages", "grounding_queries")
SERIES_SHAPES = ("date", "date+page", "date+query", "date+page+query")
TOP_N = 25


class ImportOut(BaseModel):
    id: uuid.UUID
    source: str
    filename: str
    shape: str
    row_count: int
    date_min: date | None
    date_max: date | None
    columns: list[str]
    mapping: dict[str, Any]
    created_at: datetime
    # Only on the response to an upload:
    metrics: list[str] = []
    extra_metrics: list[str] = []
    skipped_rows: int = 0
    warnings: list[str] = []
    replaced_rows: int = 0


class SeriesPoint(BaseModel):
    date: date
    values: dict[str, float]


class TopRow(BaseModel):
    key: str
    values: dict[str, float]


class AiReportOut(BaseModel):
    source: str
    imports: list[ImportOut]
    metrics: list[str]
    primary_metric: str | None
    series_shape: str | None
    series: list[SeriesPoint]
    top_pages: list[TopRow]
    top_queries: list[TopRow]


def _source(value: str) -> Source:
    if value not in SOURCES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown report type")
    return value


async def store_import(
    session: AsyncSession, site_id: uuid.UUID, parsed: ParsedReport, filename: str, user_id: uuid.UUID | None
) -> tuple[AiReportImport, int]:
    """Insert the report. Older rows of the same source and shape that this file covers are replaced, so
    uploading an overlapping export never double-counts."""
    replaced = 0
    older = sa.select(AiReportImport.id).where(
        AiReportImport.site_id == site_id,
        AiReportImport.source == parsed.source,
        AiReportImport.shape == parsed.shape,
    )
    condition = [AiReportRow.import_id.in_(older)]
    if parsed.date_min is not None and parsed.date_max is not None:
        condition.append(AiReportRow.date.between(parsed.date_min, parsed.date_max))
    result = await session.execute(sa.delete(AiReportRow).where(*condition))
    replaced = int(result.rowcount or 0)  # type: ignore[attr-defined]
    await session.execute(
        sa.delete(AiReportImport).where(
            AiReportImport.site_id == site_id,
            ~sa.exists().where(AiReportRow.import_id == AiReportImport.id),
        )
    )
    record = AiReportImport(
        site_id=site_id,
        source=parsed.source,
        filename=filename[:255],
        shape=parsed.shape,
        row_count=len(parsed.rows),
        date_min=parsed.date_min,
        date_max=parsed.date_max,
        columns=parsed.columns,
        mapping=parsed.mapping,
        uploaded_by=user_id,
    )
    session.add(record)
    await session.flush()
    values = [
        {
            "site_id": site_id,
            "source": parsed.source,
            "import_id": record.id,
            "date": r.date,
            "page": r.page,
            "query": r.query,
            "metric": r.metric,
            "value": r.value,
        }
        for r in parsed.rows
    ]
    for start in range(0, len(values), 5000):
        await session.execute(sa.insert(AiReportRow), values[start : start + 5000])
    await session.commit()
    return record, replaced


@router.post("/{source}/import", response_model=ImportOut, status_code=status.HTTP_201_CREATED)
async def import_report(
    site_id: uuid.UUID, source: str, ctx: WriteCtx, file: Annotated[UploadFile, File()]
) -> ImportOut:
    kind = _source(source)
    await ctx.get_or_404(Site, site_id, "Site")
    data = await file.read(MAX_BYTES + 1)
    try:
        parsed = parse_report(data, kind)
    except CsvFormatError as exc:
        raise HTTPException(422, str(exc)) from None
    record, replaced = await store_import(
        ctx.session, site_id, parsed, file.filename or "upload.csv", ctx.user.id
    )
    out = ImportOut.model_validate(record, from_attributes=True)
    out.metrics = parsed.metrics
    out.extra_metrics = parsed.extra_metrics
    out.skipped_rows = parsed.skipped_rows
    out.warnings = parsed.warnings
    out.replaced_rows = replaced
    return out


async def _top(
    session: AsyncSession, site_id: uuid.UUID, source: str, column: Any, primary: str | None
) -> list[TopRow]:
    metric_sum = sa.func.sum(AiReportRow.value)
    rows = await session.execute(
        sa.select(column, AiReportRow.metric, metric_sum)
        .where(
            AiReportRow.site_id == site_id,
            AiReportRow.source == source,
            column.is_not(None),
            AiReportRow.metric.in_(ADDITIVE),
        )
        .group_by(column, AiReportRow.metric)
    )
    merged: dict[str, dict[str, float]] = {}
    for key, metric, total in rows:
        merged.setdefault(str(key), {})[str(metric)] = float(total)
    ranked = sorted(merged.items(), key=lambda kv: -kv[1].get(primary or "", sum(kv[1].values())))
    return [TopRow(key=k, values=v) for k, v in ranked[:TOP_N]]


@router.get("/{source}", response_model=AiReportOut)
async def get_report(site_id: uuid.UUID, source: str, ctx: ReadCtx) -> AiReportOut:
    kind = _source(source)
    await ctx.get_or_404(Site, site_id, "Site")
    session = ctx.session
    imports = (
        await session.scalars(
            sa.select(AiReportImport)
            .where(AiReportImport.site_id == site_id, AiReportImport.source == kind)
            .order_by(AiReportImport.created_at.desc())
        )
    ).all()
    metrics = sorted(
        set(
            await session.scalars(
                sa.select(AiReportRow.metric)
                .where(AiReportRow.site_id == site_id, AiReportRow.source == kind)
                .distinct()
            )
        )
    )
    primary = next((m for m in PRIMARY_ORDER if m in metrics), metrics[0] if metrics else None)

    shape = next((s for s in SERIES_SHAPES if any(i.shape == s for i in imports)), None)
    series: list[SeriesPoint] = []
    if shape is not None:
        wanted = [AiReportRow.metric.in_(ADDITIVE)] if shape != "date" else []
        rows = await session.execute(
            sa.select(AiReportRow.date, AiReportRow.metric, sa.func.sum(AiReportRow.value))
            .join(AiReportImport, AiReportImport.id == AiReportRow.import_id)
            .where(
                AiReportRow.site_id == site_id,
                AiReportRow.source == kind,
                AiReportImport.shape == shape,
                AiReportRow.date.is_not(None),
                *wanted,
            )
            .group_by(AiReportRow.date, AiReportRow.metric)
            .order_by(AiReportRow.date)
        )
        by_day: dict[date, dict[str, float]] = {}
        for day, metric, total in rows:
            if day is None:
                continue
            by_day.setdefault(day, {})[str(metric)] = float(total)
        series = [SeriesPoint(date=d, values=v) for d, v in sorted(by_day.items())]
    return AiReportOut(
        source=kind,
        imports=[ImportOut.model_validate(i, from_attributes=True) for i in imports],
        metrics=metrics,
        primary_metric=primary,
        series_shape=shape,
        series=series,
        top_pages=await _top(session, site_id, kind, AiReportRow.page, primary),
        top_queries=await _top(session, site_id, kind, AiReportRow.query, primary),
    )


@router.delete("/imports/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_import(site_id: uuid.UUID, import_id: uuid.UUID, ctx: WriteCtx) -> Response:
    await ctx.get_or_404(Site, site_id, "Site")
    record = await ctx.session.scalar(
        sa.select(AiReportImport).where(AiReportImport.id == import_id, AiReportImport.site_id == site_id)
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    await ctx.session.delete(record)
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
