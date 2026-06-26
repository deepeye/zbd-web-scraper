"""独立库 zbd_crawler_data 的 engine 与 session 工厂。

与主库 scraper_db 完全独立，不走 Alembic。被 storage/djg_data.py（djg_data 表）
与 api/deps.py（DjgDataRepoD 依赖）复用。web_snapshot 表已移除。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from web_scraper_service.config import settings

snapshot_engine = create_async_engine(
    settings.snapshot_database_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
)

SnapshotSession = async_sessionmaker(
    snapshot_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def close_snapshot_engine() -> None:
    await snapshot_engine.dispose()
