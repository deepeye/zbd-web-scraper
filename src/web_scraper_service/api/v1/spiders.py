"""Spider management API — CRUD + run/pause/resume."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web_scraper_service.api.deps import ApiKey, Pagination, SpiderRepoD
from web_scraper_service.api.response import PaginationMeta, ok
from web_scraper_service.scheduler.engine import add_scheduled_spider, remove_scheduled_spider
from web_scraper_service.scheduler.jobs import dispatch_crawl

router = APIRouter(prefix="/spiders", tags=["Spiders"])


class SpiderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str
    schedule: str | None = None
    use_playwright: bool = False
    use_camoufox: bool = False
    use_stealthy: bool = False
    concurrency: int = 5
    proxy_enabled: bool = False
    retry_times: int = 3
    download_delay: float = 0.5
    config: dict[str, Any] | None = None
    callback_url: str | None = None


class SpiderUpdate(BaseModel):
    url: str | None = None
    schedule: str | None = None
    use_playwright: bool | None = None
    use_camoufox: bool | None = None
    use_stealthy: bool | None = None
    concurrency: int | None = None
    proxy_enabled: bool | None = None
    retry_times: int | None = None
    download_delay: float | None = None
    config: dict[str, Any] | None = None
    is_active: bool | None = None
    callback_url: str | None = None


@router.post("")
async def create_spider(body: SpiderCreate, repo: SpiderRepoD, _: ApiKey) -> dict[str, Any]:
    existing = await repo.get_by_name(body.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Spider '{body.name}' already exists")
    data = body.model_dump()
    spider = await repo.create(data)
    if spider.schedule:
        try:
            await add_scheduled_spider(spider.name, spider.schedule, data)
        except Exception as e:
            from loguru import logger
            logger.warning("Failed to schedule spider {name}: {err}", name=spider.name, err=str(e))
    return ok({"id": str(spider.id), "name": spider.name, "url": spider.url})


@router.get("")
async def list_spiders(repo: SpiderRepoD, _: ApiKey, pagination: Pagination) -> dict[str, Any]:
    total = await repo.count_all()
    spiders = await repo.list_all(limit=pagination.size, offset=pagination.offset)
    return ok(
        [
            {
                "id": str(s.id),
                "name": s.name,
                "url": s.url,
                "schedule": s.schedule,
                "use_playwright": s.use_playwright,
                "is_active": s.is_active,
                "created_at": str(s.created_at),
            }
            for s in spiders
        ],
        PaginationMeta(page=pagination.page, size=pagination.size, total=total),
    )


@router.get("/{spider_id}")
async def get_spider(spider_id: uuid.UUID, repo: SpiderRepoD, _: ApiKey) -> dict[str, Any]:
    spider = await repo.get(spider_id)
    if not spider:
        raise HTTPException(status_code=404, detail=f"Spider {spider_id} not found")
    return ok(
        {
            "id": str(spider.id),
            "name": spider.name,
            "url": spider.url,
            "schedule": spider.schedule,
            "use_playwright": spider.use_playwright,
            "use_camoufox": spider.use_camoufox,
            "use_stealthy": spider.use_stealthy,
            "concurrency": spider.concurrency,
            "proxy_enabled": spider.proxy_enabled,
            "retry_times": spider.retry_times,
            "download_delay": spider.download_delay,
            "config": spider.config,
            "is_active": spider.is_active,
            "callback_url": spider.callback_url,
            "created_at": str(spider.created_at),
            "updated_at": str(spider.updated_at),
        }
    )


@router.patch("/{spider_id}")
async def update_spider(spider_id: uuid.UUID, body: SpiderUpdate, repo: SpiderRepoD, _: ApiKey) -> dict[str, Any]:
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    spider = await repo.update(spider_id, data)
    if not spider:
        raise HTTPException(status_code=404, detail=f"Spider {spider_id} not found")
    # Update schedule if changed
    if "schedule" in data and spider.schedule:
        await remove_scheduled_spider(spider.name)
        await add_scheduled_spider(spider.name, spider.schedule, {k: v for k, v in data.items() if k != "schedule"})
    return ok({"id": str(spider.id), "name": spider.name})


@router.delete("/{spider_id}")
async def delete_spider(spider_id: uuid.UUID, repo: SpiderRepoD, _: ApiKey) -> dict[str, Any]:
    spider = await repo.get(spider_id)
    if not spider:
        raise HTTPException(status_code=404, detail=f"Spider {spider_id} not found")
    await remove_scheduled_spider(spider.name)
    deleted = await repo.delete(spider_id)
    return ok({"deleted": deleted})


@router.post("/{spider_id}/run")
async def run_spider(spider_id: uuid.UUID, repo: SpiderRepoD, _: ApiKey) -> dict[str, Any]:
    spider = await repo.get(spider_id)
    if not spider:
        raise HTTPException(status_code=404, detail=f"Spider {spider_id} not found")
    spider_config = {
        "name": spider.name,
        "start_urls": [spider.url],
        "use_playwright": spider.use_playwright,
        "use_camoufox": spider.use_camoufox,
        "use_stealthy": spider.use_stealthy,
        "concurrency": spider.concurrency,
        "proxy_enabled": spider.proxy_enabled,
        "retry_times": spider.retry_times,
        "download_delay": spider.download_delay,
    }
    job_id = await dispatch_crawl(spider.name, spider_config, trigger_type="api")
    return ok({"job_id": job_id, "spider_name": spider.name, "status": "pending"})


@router.post("/{spider_id}/pause")
async def pause_spider(spider_id: uuid.UUID, repo: SpiderRepoD, _: ApiKey) -> dict[str, Any]:
    spider = await repo.get(spider_id)
    if not spider:
        raise HTTPException(status_code=404, detail=f"Spider {spider_id} not found")
    removed = await remove_scheduled_spider(spider.name)
    await repo.update(spider_id, {"is_active": False})
    return ok({"spider_name": spider.name, "paused": removed})


@router.post("/{spider_id}/resume")
async def resume_spider(spider_id: uuid.UUID, repo: SpiderRepoD, _: ApiKey) -> dict[str, Any]:
    spider = await repo.get(spider_id)
    if not spider:
        raise HTTPException(status_code=404, detail=f"Spider {spider_id} not found")
    if spider.schedule:
        spider_config = {
            "name": spider.name,
            "start_urls": [spider.url],
            "use_playwright": spider.use_playwright,
            "use_camoufox": spider.use_camoufox,
            "use_stealthy": spider.use_stealthy,
            "concurrency": spider.concurrency,
            "proxy_enabled": spider.proxy_enabled,
            "retry_times": spider.retry_times,
            "download_delay": spider.download_delay,
        }
        await add_scheduled_spider(spider.name, spider.schedule, spider_config)
    await repo.update(spider_id, {"is_active": True})
    return ok({"spider_name": spider.name, "resumed": True})
