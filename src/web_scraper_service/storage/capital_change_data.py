"""zbd_crawler_data.capital_change_data storage."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Text, UniqueConstraint, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from web_scraper_service.storage.snapshot import snapshot_engine


class _CapitalChangeBase(DeclarativeBase):
    pass


class CapitalChangeData(_CapitalChangeBase):
    __tablename__ = "capital_change_data"
    __table_args__ = (
        UniqueConstraint(
            "doc_id",
            "institution_name",
            "change_type",
            name="uq_capital_change_doc_institution_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issue_date: Mapped[str] = mapped_column(Text, default="")
    issuing_authority: Mapped[str] = mapped_column(Text, default="")
    doc_number: Mapped[str] = mapped_column(Text, default="")
    change_type: Mapped[str] = mapped_column(Text, default="")
    institution_name: Mapped[str] = mapped_column(Text, default="")
    registered_capital_before: Mapped[str] = mapped_column(Text, default="")
    registered_capital_change_method: Mapped[str] = mapped_column(Text, default="")
    change_amount: Mapped[str] = mapped_column(Text, default="")
    registered_capital_after: Mapped[str] = mapped_column(Text, default="")
    doc_title: Mapped[str] = mapped_column(Text, default="")
    doc_url: Mapped[str] = mapped_column(Text, default="")
    crawl_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


async def init_capital_change_table() -> None:
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_CapitalChangeBase.metadata.create_all)
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_capital_change_data_publish_date "
                "ON capital_change_data (publish_date DESC NULLS LAST)"
            )
        )


class CapitalChangeDataRepo:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def existing_doc_ids(self, doc_ids: set[int]) -> set[int]:
        if not doc_ids:
            return set()
        stmt = (
            select(CapitalChangeData.doc_id).where(CapitalChangeData.doc_id.in_(doc_ids)).distinct()
        )
        result = await self.session.execute(stmt)
        return {row[0] for row in result.all()}

    async def list_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[CapitalChangeData]:
        """按 publish_date 范围查询，最新发布在前，NULL 置后。"""
        stmt = select(CapitalChangeData)
        if start_date is not None:
            stmt = stmt.where(CapitalChangeData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(CapitalChangeData.publish_date <= end_date)
        stmt = (
            stmt.order_by(
                CapitalChangeData.publish_date.desc().nulls_last(), CapitalChangeData.id.desc()
            )
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
        stmt = select(func.count()).select_from(CapitalChangeData)
        if start_date is not None:
            stmt = stmt.where(CapitalChangeData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(CapitalChangeData.publish_date <= end_date)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def insert_many(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        stmt = pg_insert(CapitalChangeData).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_capital_change_doc_institution_type")
        result = await self.session.execute(stmt)
        await self.session.commit()
        return getattr(result, "rowcount", 0) or 0
