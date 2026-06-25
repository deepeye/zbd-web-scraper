from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from web_scraper_service.storage.models import ItemModel, JobModel, MetricsModel, SpiderModel


_SPIDER_UPDATABLE = frozenset({
    "url", "schedule", "use_playwright", "use_camoufox", "use_stealthy",
    "concurrency", "proxy_enabled", "retry_times", "download_delay",
    "config", "is_active", "callback_url",
})
_SPIDER_CREATABLE = _SPIDER_UPDATABLE | {"name"}

_JOB_CREATABLE = frozenset({
    "id", "spider_id", "spider_name", "status", "trigger_type",
    "requests_total", "requests_success", "requests_failed",
    "items_scraped", "items_stored", "items_deduped",
    "error_message", "result", "started_at", "finished_at",
})

_ITEM_CREATABLE = frozenset({
    "id", "job_id", "spider_name", "url", "data", "content_hash",
})


class SpiderRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: dict[str, Any]) -> SpiderModel:
        safe = {k: v for k, v in data.items() if k in _SPIDER_CREATABLE}
        spider = SpiderModel(**safe)
        self.session.add(spider)
        await self.session.flush()
        return spider

    async def get(self, spider_id: uuid.UUID) -> SpiderModel | None:
        return await self.session.get(SpiderModel, spider_id)

    async def get_by_name(self, name: str) -> SpiderModel | None:
        stmt = select(SpiderModel).where(SpiderModel.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, active_only: bool = False, limit: int = 100, offset: int = 0) -> list[SpiderModel]:
        stmt = select(SpiderModel).order_by(SpiderModel.created_at.desc()).limit(limit).offset(offset)
        if active_only:
            stmt = stmt.where(SpiderModel.is_active.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_all(self, active_only: bool = False) -> int:
        stmt = select(func.count()).select_from(SpiderModel)
        if active_only:
            stmt = stmt.where(SpiderModel.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def update(self, spider_id: uuid.UUID, data: dict[str, Any]) -> SpiderModel | None:
        safe = {k: v for k, v in data.items() if k in _SPIDER_UPDATABLE}
        if not safe:
            return await self.get(spider_id)
        spider = await self.get(spider_id)
        if not spider:
            return None
        for key, value in safe.items():
            setattr(spider, key, value)
        await self.session.flush()
        return spider

    async def delete(self, spider_id: uuid.UUID) -> bool:
        spider = await self.get(spider_id)
        if not spider:
            return False
        await self.session.delete(spider)
        await self.session.flush()
        return True


class JobRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: dict[str, Any]) -> JobModel:
        safe = {k: v for k, v in data.items() if k in _JOB_CREATABLE}
        job = JobModel(**safe)
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> JobModel | None:
        return await self.session.get(JobModel, job_id)

    async def get_running(self, spider_id: uuid.UUID) -> JobModel | None:
        stmt = select(JobModel).where(
            JobModel.spider_id == spider_id,
            JobModel.status.in_(["pending", "running"]),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_spider(
        self, spider_id: uuid.UUID, limit: int = 20, offset: int = 0
    ) -> list[JobModel]:
        stmt = (
            select(JobModel)
            .where(JobModel.spider_id == spider_id)
            .order_by(JobModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self, limit: int = 20, offset: int = 0) -> list[JobModel]:
        stmt = select(JobModel).order_by(JobModel.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_all(self) -> int:
        stmt = select(func.count()).select_from(JobModel)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def update_status(self, job_id: uuid.UUID, status: str, **kwargs: Any) -> None:
        values: dict[str, Any] = {"status": status}
        if status == "running":
            values["started_at"] = datetime.now(UTC)
        if status in ("completed", "failed", "cancelled"):
            values["finished_at"] = datetime.now(UTC)
        values.update(kwargs)
        stmt = update(JobModel).where(JobModel.id == job_id).values(**values)
        await self.session.execute(stmt)
        await self.session.flush()

    async def count_by_spider(self, spider_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(JobModel).where(JobModel.spider_id == spider_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()


class ItemRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: dict[str, Any]) -> ItemModel:
        safe = {k: v for k, v in data.items() if k in _ITEM_CREATABLE}
        item = ItemModel(**safe)
        self.session.add(item)
        await self.session.flush()
        return item

    async def bulk_create(self, items: list[dict[str, Any]]) -> list[ItemModel]:
        models = [ItemModel(**{k: v for k, v in d.items() if k in _ITEM_CREATABLE}) for d in items]
        self.session.add_all(models)
        await self.session.flush()
        return models

    async def list_by_job(
        self,
        job_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ItemModel]:
        stmt = (
            select(ItemModel)
            .where(ItemModel.job_id == job_id)
            .order_by(ItemModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_spider(
        self,
        spider_name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ItemModel]:
        stmt = select(ItemModel).where(ItemModel.spider_name == spider_name)
        if start_date:
            stmt = stmt.where(ItemModel.created_at >= start_date)
        if end_date:
            stmt = stmt.where(ItemModel.created_at <= end_date)
        stmt = stmt.order_by(ItemModel.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_spider(
        self,
        spider_name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(ItemModel).where(ItemModel.spider_name == spider_name)
        if start_date:
            stmt = stmt.where(ItemModel.created_at >= start_date)
        if end_date:
            stmt = stmt.where(ItemModel.created_at <= end_date)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def exists_by_hash(self, content_hash: str) -> bool:
        stmt = select(func.count()).select_from(ItemModel).where(ItemModel.content_hash == content_hash).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one() > 0


class MetricsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, data: dict[str, Any]) -> MetricsModel:
        metric = MetricsModel(**data)
        self.session.add(metric)
        await self.session.flush()
        return metric

    async def list_by_job(self, job_id: uuid.UUID) -> list[MetricsModel]:
        stmt = select(MetricsModel).where(MetricsModel.job_id == job_id).order_by(MetricsModel.recorded_at)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_spider(
        self, spider_name: str, limit: int = 100, offset: int = 0
    ) -> list[MetricsModel]:
        stmt = (
            select(MetricsModel)
            .where(MetricsModel.spider_name == spider_name)
            .order_by(MetricsModel.recorded_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
