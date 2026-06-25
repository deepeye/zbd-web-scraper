"""独立库 zbd_crawler_data 的快照存储：engine、模型、仓库。

与主库 scraper_db 完全独立，不走 Alembic；表由 init_table() 用
CREATE TABLE IF NOT EXISTS 创建。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Text, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from web_scraper_service.config import settings


class _SnapshotBase(DeclarativeBase):
    pass


class WebSnapshot(_SnapshotBase):
    __tablename__ = "web_snapshot"

    doc_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    crawl_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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


async def init_table() -> None:
    """CREATE TABLE IF NOT EXISTS web_snapshot."""
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_SnapshotBase.metadata.create_all)


class SnapshotRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def existing_doc_ids(self, doc_ids: set[int]) -> set[int]:
        """返回 doc_ids 中已存在于 web_snapshot 的 doc_id 集合。"""
        if not doc_ids:
            return set()
        stmt = select(WebSnapshot.doc_id).where(WebSnapshot.doc_id.in_(doc_ids))
        result = await self.session.execute(stmt)
        return {row[0] for row in result.all()}

    async def insert_many(self, rows: list[dict[str, Any]]) -> int:
        """批量插入，已存在 doc_id 跳过（ON CONFLICT DO NOTHING）。返回实际新增行数。"""
        if not rows:
            return 0
        stmt = pg_insert(WebSnapshot).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["doc_id"])
        result = await self.session.execute(stmt)
        await self.session.commit()
        return getattr(result, "rowcount", 0) or 0


async def close_snapshot_engine() -> None:
    await snapshot_engine.dispose()
