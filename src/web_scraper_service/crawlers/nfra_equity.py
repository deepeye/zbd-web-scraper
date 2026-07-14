"""nfra equity (shareholder) change / opening shareholders crawler orchestration."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from web_scraper_service.crawlers.nfra import (
    build_detail_html_url,
    discover_doc_rows,
)
from web_scraper_service.crawlers.nfra_equity_extractor import (
    extract_rows_llm,
    is_equity_candidate,
)
from web_scraper_service.storage.equity_change_data import (
    EquityChangeDataRepo,
    init_equity_change_table,
)
from web_scraper_service.storage.snapshot import SnapshotSession

DEFAULT_ITEM_IDS = (4110, 4291)


async def _fetch_detail_rows(
    session: Any,
    doc_id: int,
    doc_url: str,
    download_delay: float,
) -> list[dict[str, Any]]:
    try:
        resp = await session.fetch(doc_url, network_idle=True, timeout=60000)
        html = resp.html_content or ""
        return await extract_rows_llm(doc_id, html, doc_url)
    except Exception as exc:
        logger.error("股权变更详情 doc_id={} 抽取失败: {}", doc_id, exc)
        return []
    finally:
        if download_delay > 0:
            await asyncio.sleep(download_delay)


async def run_crawl(
    item_id: int | None = None,
    pages: int = 5,
    concurrency: int = 2,
    download_delay: float = 1.0,
) -> dict[str, Any]:
    from scrapling.fetchers import AsyncDynamicSession

    await init_equity_change_table()

    item_ids = (item_id,) if item_id is not None else DEFAULT_ITEM_IDS
    rows: list[dict[str, Any]] = []
    for current_item_id in item_ids:
        rows.extend(await discover_doc_rows(current_item_id, pages))

    if not rows:
        return {"discovered": 0, "qualified": 0, "pending": 0, "extracted_rows": 0, "stored": 0}

    qualified = [row for row in rows if is_equity_candidate(row.get("docTitle", ""))]
    if not qualified:
        return {"discovered": len(rows), "qualified": 0, "pending": 0, "extracted_rows": 0, "stored": 0}

    pending_ids = {int(row["docId"]) for row in qualified}
    async with SnapshotSession() as db:
        repo = EquityChangeDataRepo(db)
        existing = await repo.existing_doc_ids(pending_ids)
    pending = [row for row in qualified if int(row["docId"]) not in existing]
    if not pending:
        return {"discovered": len(rows), "qualified": len(qualified), "pending": 0, "extracted_rows": 0, "stored": 0}

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(row: dict[str, Any]) -> tuple[int, int]:
        doc_id = int(row["docId"])
        async with sem:
            batch = await _fetch_detail_rows(
                session,
                doc_id,
                build_detail_html_url(doc_id),
                download_delay,
            )
        if not batch:
            return 0, 0
        async with SnapshotSession() as db:
            repo = EquityChangeDataRepo(db)
            stored = await repo.insert_many(batch)
        logger.info("equity doc_id={} 抽取 {} 行，写入 {} 行", doc_id, len(batch), stored)
        return len(batch), stored

    async with AsyncDynamicSession(headless=True) as session:
        results = await asyncio.gather(*(_guarded(row) for row in pending))

    extracted_rows = sum(result[0] for result in results)
    stored = sum(result[1] for result in results)
    return {
        "discovered": len(rows),
        "qualified": len(qualified),
        "pending": len(pending),
        "extracted_rows": extracted_rows,
        "stored": stored,
    }
