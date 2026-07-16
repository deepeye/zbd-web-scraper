"""nfra crawl API — manual trigger + job status."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from celery.result import AsyncResult  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web_scraper_service.api.deps import (
    ApiKey,
    CapitalChangeDataRepoD,
    DjgDataRepoD,
    EquityChangeDataRepoD,
    Pagination,
)
from web_scraper_service.api.response import PaginationMeta, ok
from web_scraper_service.scheduler.engine import (
    celery_app,
    nfra_capital_crawl_task,
    nfra_crawl_task,
    nfra_equity_crawl_task,
)

router = APIRouter(prefix="/nfra", tags=["nfra"])


class CrawlRequest(BaseModel):
    item_id: int = Field(default=4110)
    start_page: int = Field(default=1, ge=1)
    end_page: int = Field(default=5, ge=1)


class CapitalCrawlRequest(BaseModel):
    item_id: int | None = Field(default=None)
    start_page: int = Field(default=1, ge=1)
    end_page: int = Field(default=5, ge=1)


class EquityCrawlRequest(BaseModel):
    item_id: int | None = Field(default=None)
    start_page: int = Field(default=1, ge=1)
    end_page: int = Field(default=5, ge=1)


@router.post("/djg/crawl")
async def crawl(body: CrawlRequest, _: ApiKey) -> dict[str, Any]:
    if body.start_page < 1:
        raise HTTPException(status_code=400, detail="start_page must be >= 1")
    if body.end_page < body.start_page:
        raise HTTPException(status_code=400, detail="end_page must be >= start_page")
    if body.item_id < 1:
        raise HTTPException(status_code=400, detail="item_id must be >= 1")
    job_id = str(uuid.uuid4())
    result = nfra_crawl_task.apply_async(
        args=[body.item_id, body.start_page, body.end_page], task_id=job_id
    )
    return ok(
        {
            "job_id": result.id,
            "item_id": body.item_id,
            "start_page": body.start_page,
            "end_page": body.end_page,
            "status": "pending",
        }
    )


@router.get("/djg/crawl/{job_id}")
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


@router.post("/capital/crawl")
async def capital_crawl(body: CapitalCrawlRequest, _: ApiKey) -> dict[str, Any]:
    if body.start_page < 1:
        raise HTTPException(status_code=400, detail="start_page must be >= 1")
    if body.end_page < body.start_page:
        raise HTTPException(status_code=400, detail="end_page must be >= start_page")
    if body.item_id is not None and body.item_id < 1:
        raise HTTPException(status_code=400, detail="item_id must be >= 1")
    job_id = str(uuid.uuid4())
    result = nfra_capital_crawl_task.apply_async(
        args=[body.item_id, body.start_page, body.end_page], task_id=job_id
    )
    return ok(
        {
            "job_id": result.id,
            "item_id": body.item_id,
            "start_page": body.start_page,
            "end_page": body.end_page,
            "status": "pending",
        }
    )


@router.get("/capital/crawl/{job_id}")
async def capital_crawl_status(job_id: str, _: ApiKey) -> dict[str, Any]:
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


@router.get("/capital/data")
async def list_capital_data(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    repo: CapitalChangeDataRepoD = None,  # type: ignore[assignment]
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
                "id": str(row.id),
                "doc_id": row.doc_id,
                "publish_date": row.publish_date.isoformat() if row.publish_date else None,
                "issue_date": row.issue_date,
                "issuing_authority": row.issuing_authority,
                "doc_number": row.doc_number,
                "change_type": row.change_type,
                "institution_name": row.institution_name,
                "registered_capital_before": row.registered_capital_before,
                "registered_capital_change_method": row.registered_capital_change_method,
                "change_amount": row.change_amount,
                "registered_capital_after": row.registered_capital_after,
                "doc_title": row.doc_title,
                "doc_url": row.doc_url,
                "crawl_time": str(row.crawl_time),
            }
            for row in rows
        ],
        PaginationMeta(page=pagination.page, size=pagination.size, total=total),
    )


@router.post("/equity/crawl")
async def equity_crawl(body: EquityCrawlRequest, _: ApiKey) -> dict[str, Any]:
    if body.start_page < 1:
        raise HTTPException(status_code=400, detail="start_page must be >= 1")
    if body.end_page < body.start_page:
        raise HTTPException(status_code=400, detail="end_page must be >= start_page")
    if body.item_id is not None and body.item_id < 1:
        raise HTTPException(status_code=400, detail="item_id must be >= 1")
    job_id = str(uuid.uuid4())
    result = nfra_equity_crawl_task.apply_async(
        args=[body.item_id, body.start_page, body.end_page], task_id=job_id
    )
    return ok(
        {
            "job_id": result.id,
            "item_id": body.item_id,
            "start_page": body.start_page,
            "end_page": body.end_page,
            "status": "pending",
        }
    )


@router.get("/equity/crawl/{job_id}")
async def equity_crawl_status(job_id: str, _: ApiKey) -> dict[str, Any]:
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


@router.get("/equity/data")
async def list_equity_data(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    repo: EquityChangeDataRepoD = None,  # type: ignore[assignment]
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
                "id": str(row.id),
                "doc_id": row.doc_id,
                "publish_date": row.publish_date.isoformat() if row.publish_date else None,
                "issue_date": row.issue_date,
                "issuing_authority": row.issuing_authority,
                "doc_number": row.doc_number,
                "change_type": row.change_type,
                "institution_name": row.institution_name,
                "shareholder_name": row.shareholder_name,
                "shareholding_before": row.shareholding_before,
                "change_method": row.change_method,
                "transferred_shares": row.transferred_shares,
                "transferred_ratio": row.transferred_ratio,
                "shares_after": row.shares_after,
                "shareholding_after": row.shareholding_after,
                "contribution_amount": row.contribution_amount,
                "doc_title": row.doc_title,
                "doc_url": row.doc_url,
                "crawl_time": str(row.crawl_time),
            }
            for row in rows
        ],
        PaginationMeta(page=pagination.page, size=pagination.size, total=total),
    )


@router.get("/djg/data")
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
