"""nfra crawl API — manual trigger + job status."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from celery.result import AsyncResult  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web_scraper_service.api.deps import ApiKey, DjgDataRepoD, Pagination
from web_scraper_service.api.response import PaginationMeta, ok
from web_scraper_service.scheduler.engine import celery_app, nfra_crawl_task

router = APIRouter(prefix="/nfra", tags=["nfra"])


class CrawlRequest(BaseModel):
    item_id: int = Field(default=4110)
    pages: int = Field(default=5)


@router.post("/crawl")
async def crawl(body: CrawlRequest, _: ApiKey) -> dict[str, Any]:
    if body.pages < 1:
        raise HTTPException(status_code=400, detail="pages must be >= 1")
    if body.item_id < 1:
        raise HTTPException(status_code=400, detail="item_id must be >= 1")
    job_id = str(uuid.uuid4())
    result = nfra_crawl_task.apply_async(
        args=[body.item_id, body.pages], task_id=job_id
    )
    return ok(
        {
            "job_id": result.id,
            "item_id": body.item_id,
            "pages": body.pages,
            "status": "pending",
        }
    )


@router.get("/crawl/{job_id}")
async def crawl_status(job_id: str, _: ApiKey) -> dict[str, Any]:
    async_res = AsyncResult(job_id, app=celery_app)
    state = async_res.state or "PENDING"
    status_map = {
        "PENDING": "pending",
        "STARTED": "running",
        "SUCCESS": "success",
        "FAILURE": "failed",
        "RETRY": "retrying",
    }
    status = status_map.get(state, state.lower())
    result: Any = None
    if state == "SUCCESS":
        result = async_res.result
    elif state == "FAILURE":
        result = str(async_res.result)
    return ok({"job_id": job_id, "status": status, "result": result})


@router.get("/data")
async def list_data(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    repo: DjgDataRepoD = None,  # type: ignore[assignment]
    _: ApiKey = None,  # type: ignore[assignment]
    pagination: Pagination = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    rows = await repo.list_by_crawl_time(
        start_date=start_date,
        end_date=end_date,
        limit=pagination.size,
        offset=pagination.offset,
    )
    total = await repo.count_by_crawl_time(start_date=start_date, end_date=end_date)
    return ok(
        [
            {
                "id": str(r.id),
                "doc_id": r.doc_id,
                "publish_date": r.publish_date.isoformat() if r.publish_date else None,
                "issue_date": r.issue_date,
                "issuing_authority": r.issuing_authority,
                "doc_number": r.doc_number,
                "institution_name": r.institution_name,
                "person_name": r.person_name,
                "position": r.position,
                "doc_title": r.doc_title,
                "doc_url": r.doc_url,
                "crawl_time": str(r.crawl_time),
            }
            for r in rows
        ],
        PaginationMeta(page=pagination.page, size=pagination.size, total=total),
    )
