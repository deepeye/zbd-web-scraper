"""FastAPI dependency injection."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from web_scraper_service.config import settings
from web_scraper_service.core.exceptions import SpiderNotFoundError
from web_scraper_service.storage.database import get_session
from web_scraper_service.storage.repositories import ItemRepo, JobRepo, MetricsRepo, SpiderRepo


async def get_db() -> AsyncSession:
    async for session in get_session():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_spider_repo(session: DbSession) -> SpiderRepo:
    return SpiderRepo(session)


def get_job_repo(session: DbSession) -> JobRepo:
    return JobRepo(session)


def get_item_repo(session: DbSession) -> ItemRepo:
    return ItemRepo(session)


def get_metrics_repo(session: DbSession) -> MetricsRepo:
    return MetricsRepo(session)


SpiderRepoD = Annotated[SpiderRepo, Depends(get_spider_repo)]
JobRepoD = Annotated[JobRepo, Depends(get_job_repo)]
ItemRepoD = Annotated[ItemRepo, Depends(get_item_repo)]
MetricsRepoD = Annotated[MetricsRepo, Depends(get_metrics_repo)]


async def verify_api_key(x_api_key: Annotated[str | None, Header()] = None) -> str:
    if not settings.api_key:
        raise HTTPException(status_code=500, detail="API key not configured — set API_KEY env var")
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


ApiKey = Annotated[str, Depends(verify_api_key)]


class PaginationParams:
    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number"),
        size: int = Query(20, ge=1, le=100, description="Items per page"),
    ) -> None:
        self.page = page
        self.size = size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


Pagination = Annotated[PaginationParams, Depends()]
