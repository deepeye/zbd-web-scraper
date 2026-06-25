"""Job management API — list, detail, cancel."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from web_scraper_service.api.deps import ApiKey, JobRepoD, Pagination
from web_scraper_service.api.response import PaginationMeta, ok

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get("")
async def list_jobs(spider_id: uuid.UUID | None = None, repo: JobRepoD = None, _: ApiKey = None, pagination: Pagination = None) -> dict[str, Any]:
    if spider_id:
        jobs = await repo.list_by_spider(spider_id, limit=pagination.size, offset=pagination.offset)
        total = await repo.count_by_spider(spider_id)
    else:
        jobs = await repo.list_all(limit=pagination.size, offset=pagination.offset)
        total = await repo.count_all()
    return ok(
        [
            {
                "id": str(j.id),
                "spider_id": str(j.spider_id),
                "spider_name": j.spider_name,
                "status": j.status,
                "trigger_type": j.trigger_type,
                "requests_total": j.requests_total,
                "items_scraped": j.items_scraped,
                "started_at": str(j.started_at) if j.started_at else None,
                "finished_at": str(j.finished_at) if j.finished_at else None,
                "created_at": str(j.created_at),
            }
            for j in jobs
        ],
        PaginationMeta(page=pagination.page, size=pagination.size, total=total),
    )


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, repo: JobRepoD, _: ApiKey) -> dict[str, Any]:
    job = await repo.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"code": 2001, "message": "Job not found"})
    return ok(
        {
            "id": str(job.id),
            "spider_id": str(job.spider_id),
            "spider_name": job.spider_name,
            "status": job.status,
            "trigger_type": job.trigger_type,
            "requests_total": job.requests_total,
            "requests_success": job.requests_success,
            "requests_failed": job.requests_failed,
            "items_scraped": job.items_scraped,
            "items_stored": job.items_stored,
            "items_deduped": job.items_deduped,
            "error_message": job.error_message,
            "result": job.result,
            "started_at": str(job.started_at) if job.started_at else None,
            "finished_at": str(job.finished_at) if job.finished_at else None,
            "created_at": str(job.created_at),
        }
    )


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: uuid.UUID, repo: JobRepoD, _: ApiKey) -> dict[str, Any]:
    job = await repo.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"code": 2001, "message": "Job not found"})
    if job.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail={"code": 2001, "message": "Job cannot be cancelled in current state"})
    # In production: revoke the Celery task
    await repo.update_status(job_id, "cancelled")
    return ok({"job_id": str(job_id), "status": "cancelled"})
