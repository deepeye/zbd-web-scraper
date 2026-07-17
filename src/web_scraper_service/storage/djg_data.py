"""djg_data 表存储：nfra 任职资格批复抽取结果（一人一行）。

与 web_snapshot 同库 zbd_crawler_data，复用 snapshot_engine，不走 Alembic；
表由 init_djg_table() 用 CREATE TABLE IF NOT EXISTS 创建。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Text, UniqueConstraint, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from web_scraper_service.storage.snapshot import snapshot_engine


class _DjgBase(DeclarativeBase):
    pass


class DjgData(_DjgBase):
    __tablename__ = "djg_data"
    __table_args__ = (UniqueConstraint("doc_id", "person_name", name="uq_djg_doc_person"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issue_date: Mapped[str] = mapped_column(Text, default="")
    issuing_authority: Mapped[str] = mapped_column(Text, default="")
    doc_number: Mapped[str] = mapped_column(Text, default="")
    institution_name: Mapped[str] = mapped_column(Text, default="")
    person_name: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[str] = mapped_column(Text, default="")
    doc_title: Mapped[str] = mapped_column(Text, default="")
    doc_url: Mapped[str] = mapped_column(Text, default="")
    crawl_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


async def init_djg_table() -> None:
    """CREATE TABLE IF NOT EXISTS djg_data."""
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_DjgBase.metadata.create_all)
        await conn.execute(text("ALTER TABLE djg_data ADD COLUMN IF NOT EXISTS publish_date DATE"))
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_djg_data_publish_date "
                "ON djg_data (publish_date DESC NULLS LAST)"
            )
        )


class DjgDataRepo:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def existing_doc_ids(self, doc_ids: set[int]) -> set[int]:
        """返回 doc_ids 中已存在任一行于 djg_data 的 doc_id 集合。"""
        if not doc_ids:
            return set()
        stmt = select(DjgData.doc_id).where(DjgData.doc_id.in_(doc_ids)).distinct()
        result = await self.session.execute(stmt)
        return {row[0] for row in result.all()}

    async def list_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[DjgData]:
        """按 publish_date 范围查询，最新发布在前，NULL 置后。"""
        stmt = select(DjgData)
        if start_date is not None:
            stmt = stmt.where(DjgData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(DjgData.publish_date <= end_date)
        stmt = (
            stmt.order_by(DjgData.publish_date.desc().nulls_last(), DjgData.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> int:
        """按 publish_date 范围计数。"""
        stmt = select(func.count()).select_from(DjgData)
        if start_date is not None:
            stmt = stmt.where(DjgData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(DjgData.publish_date <= end_date)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def insert_many(self, rows: list[dict[str, Any]]) -> int:
        """批量插入，已存在 (doc_id,person_name) 跳过。返回新增行数。"""
        if not rows:
            return 0
        stmt = pg_insert(DjgData).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_djg_doc_person"
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return getattr(result, "rowcount", 0) or 0
