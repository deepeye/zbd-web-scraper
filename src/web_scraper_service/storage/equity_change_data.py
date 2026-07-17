"""zbd_crawler_data.equity_change_data storage."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Text, UniqueConstraint, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from web_scraper_service.storage.snapshot import snapshot_engine


class _EquityChangeBase(DeclarativeBase):
    pass


class EquityChangeData(_EquityChangeBase):
    __tablename__ = "equity_change_data"
    __table_args__ = (
        UniqueConstraint(
            "doc_id",
            "institution_name",
            "shareholder_name",
            "change_method",
            name="uq_equity_change_doc_institution_shareholder_method",
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
    shareholder_name: Mapped[str] = mapped_column(Text, default="")
    shareholding_before: Mapped[str] = mapped_column(Text, default="")
    change_method: Mapped[str] = mapped_column(Text, default="")
    transferred_shares: Mapped[str] = mapped_column(Text, default="")
    transferred_ratio: Mapped[str] = mapped_column(Text, default="")
    shares_after: Mapped[str] = mapped_column(Text, default="")
    shareholding_after: Mapped[str] = mapped_column(Text, default="")
    contribution_amount: Mapped[str] = mapped_column(Text, default="")
    doc_title: Mapped[str] = mapped_column(Text, default="")
    doc_url: Mapped[str] = mapped_column(Text, default="")
    crawl_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


async def init_equity_change_table() -> None:
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_EquityChangeBase.metadata.create_all)
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_equity_change_data_publish_date "
                "ON equity_change_data (publish_date DESC NULLS LAST)"
            )
        )


class EquityChangeDataRepo:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def existing_doc_ids(self, doc_ids: set[int]) -> set[int]:
        if not doc_ids:
            return set()
        stmt = select(EquityChangeData.doc_id).where(
            EquityChangeData.doc_id.in_(doc_ids)
        ).distinct()
        result = await self.session.execute(stmt)
        return {row[0] for row in result.all()}

    async def list_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[EquityChangeData]:
        """按 publish_date 范围查询，最新发布在前，NULL 置后。"""
        stmt = select(EquityChangeData)
        if start_date is not None:
            stmt = stmt.where(EquityChangeData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(EquityChangeData.publish_date <= end_date)
        stmt = (
            stmt.order_by(
                EquityChangeData.publish_date.desc().nulls_last(), EquityChangeData.id.desc()
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
        stmt = select(func.count()).select_from(EquityChangeData)
        if start_date is not None:
            stmt = stmt.where(EquityChangeData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(EquityChangeData.publish_date <= end_date)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def insert_many(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        stmt = pg_insert(EquityChangeData).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_equity_change_doc_institution_shareholder_method"
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return getattr(result, "rowcount", 0) or 0
