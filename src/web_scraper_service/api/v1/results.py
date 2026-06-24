"""Results query & export API."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from web_scraper_service.api.deps import ApiKey, ItemRepoD, Pagination
from web_scraper_service.api.response import PaginationMeta, ok

router = APIRouter(prefix="/results", tags=["Results"])


@router.get("")
async def list_results(
    spider_name: str = Query(..., description="Spider name to filter by"),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    repo: ItemRepoD = None,
    _: ApiKey = None,
    pagination: Pagination = None,
) -> dict[str, Any]:
    items = await repo.list_by_spider(
        spider_name,
        start_date=start_date,
        end_date=end_date,
        limit=pagination.size,
        offset=pagination.offset,
    )
    total = await repo.count_by_spider(spider_name, start_date=start_date, end_date=end_date)
    return ok(
        [
            {
                "id": str(i.id),
                "job_id": str(i.job_id),
                "spider_name": i.spider_name,
                "url": i.url,
                "data": i.data,
                "content_hash": i.content_hash,
                "created_at": str(i.created_at),
            }
            for i in items
        ],
        PaginationMeta(page=pagination.page, size=pagination.size, total=total),
    )


@router.get("/export")
async def export_results(
    spider_name: str = Query(...),
    format: str = Query("csv", pattern="^(csv|json)$"),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    repo: ItemRepoD = None,
    _: ApiKey = None,
) -> StreamingResponse:
    items = await repo.list_by_spider(
        spider_name,
        start_date=start_date,
        end_date=end_date,
        limit=10000,
        offset=0,
    )
    if not items:
        raise HTTPException(status_code=404, detail="No results found")

    if format == "csv":
        output = io.StringIO()
        if items:
            fieldnames = ["id", "job_id", "spider_name", "url", "data", "content_hash", "created_at"]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for item in items:
                writer.writerow({
                    "id": str(item.id),
                    "job_id": str(item.job_id),
                    "spider_name": item.spider_name,
                    "url": item.url,
                    "data": json.dumps(item.data, ensure_ascii=False),
                    "content_hash": item.content_hash or "",
                    "created_at": str(item.created_at),
                })
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={spider_name}_results.csv"},
        )

    # JSON Lines format
    def json_generator() -> Any:
        for item in items:
            yield json.dumps(
                {
                    "id": str(item.id),
                    "job_id": str(item.job_id),
                    "spider_name": item.spider_name,
                    "url": item.url,
                    "data": item.data,
                    "content_hash": item.content_hash,
                    "created_at": str(item.created_at),
                },
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        json_generator(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename={spider_name}_results.jsonl"},
    )
