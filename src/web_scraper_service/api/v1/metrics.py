"""Monitoring metrics API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query

from web_scraper_service.api.deps import ApiKey, JobRepoD, MetricsRepoD, Pagination
from web_scraper_service.api.response import PaginationMeta, ok

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("")
async def list_metrics(
    spider_name: str | None = None,
    job_id: uuid.UUID | None = None,
    repo: MetricsRepoD = None,
    _: ApiKey = None,
    pagination: Pagination = None,
) -> dict[str, Any]:
    if job_id:
        metrics = await repo.list_by_job(job_id)
    elif spider_name:
        metrics = await repo.list_by_spider(spider_name, limit=pagination.size, offset=pagination.offset)
    else:
        metrics = []

    return ok(
        [
            {
                "id": str(m.id),
                "job_id": str(m.job_id),
                "spider_name": m.spider_name,
                "metric_type": m.metric_type,
                "value": m.value,
                "labels": m.labels,
                "recorded_at": str(m.recorded_at),
            }
            for m in metrics
        ]
    )


@router.get("/summary/{spider_name}")
async def spider_metrics_summary(spider_name: str, job_repo: JobRepoD, metrics_repo: MetricsRepoD, _: ApiKey) -> dict[str, Any]:
    """Aggregate metrics summary for a spider across all jobs."""
    metrics = await metrics_repo.list_by_spider(spider_name, limit=1000, offset=0)
    summary: dict[str, float] = {}
    for m in metrics:
        key = m.metric_type
        summary[key] = summary.get(key, 0) + m.value
    return ok({"spider_name": spider_name, "metrics": summary, "total_records": len(metrics)})
