"""nfra crawl API — manual trigger + job status."""

from __future__ import annotations

import uuid
from typing import Any

from celery.result import AsyncResult  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web_scraper_service.api.deps import ApiKey
from web_scraper_service.api.response import ok
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
