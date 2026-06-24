"""nfra.gov.cn 文档快照采集：纯逻辑 + 异步编排。

纯逻辑（build_*/parse_doc_ids/filter_pending）可单测；异步编排
(discover_doc_ids / fetch_snapshots / run_crawl) 涉及网络与浏览器，
靠手动 smoke 验收。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from web_scraper_service.storage.snapshot import (
    SnapshotRepo,
    SnapshotSession,
    init_table,
)

BASE = "https://www.nfra.gov.cn"


def build_list_url(item_id: int, page: int, page_size: int = 18) -> str:
    return (
        f"{BASE}/cn/static/data/DocInfo/SelectDocByItemIdAndChild/"
        f"data_itemId={item_id},pageIndex={page},pageSize={page_size}.json"
    )


def build_detail_url(doc_id: int) -> str:
    return f"{BASE}/cn/static/data/DocInfo/SelectByDocId/data_docId={doc_id}.json"


def build_list_html_url(item_id: int) -> str:
    return (
        f"{BASE}/cn/view/pages/ItemList.html?itemPId=923&itemId={item_id}"
        f"&itemUrl=ItemListRightList.html&itemName=zhujiguan"
    )


def parse_doc_ids(body: str | bytes) -> list[int]:
    """解析列表接口响应，返回 docId 列表。rptCode!=200、无 rows、非法 JSON 一律返回 []。"""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:
            return []
    try:
        payload: dict[str, Any] = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    if payload.get("rptCode") != 200:
        return []
    rows = (payload.get("data") or {}).get("rows") or []
    ids: list[int] = []
    for row in rows:
        doc_id = row.get("docId")
        if isinstance(doc_id, int):
            ids.append(doc_id)
    return ids


def filter_pending(doc_ids: list[int], existing: set[int]) -> list[int]:
    """去列表内重复 + 去已存在 doc_id，保持首次出现顺序。"""
    seen: set[int] = set()
    out: list[int] = []
    for doc_id in doc_ids:
        if doc_id in existing or doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
    return out


# ── 异步编排（网络/浏览器，靠手动 smoke 验收）──────────────

_LIST_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE}/cn/view/pages/ItemList.html",
}

_DETAIL_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
}


async def discover_doc_ids(
    session: Any,
    item_id: int,
    pages: int,
) -> list[int]:
    """用浏览器持久会话遍历列表 API，收集 docId。

    先 fetch HTML 列表页触发 JS 生成会话 cookie，再逐页 fetch 列表 API。
    某页解析为空或失败则停止翻页。
    """
    doc_ids: list[int] = []
    # 1) 触发 cookie
    await session.fetch(build_list_html_url(item_id))
    # 2) 逐页采集
    for page in range(1, pages + 1):
        url = build_list_url(item_id, page)
        try:
            resp = await session.fetch(url, extra_headers=_LIST_HEADERS)
            ids = parse_doc_ids(resp.body)
        except Exception as exc:
            logger.warning("列表第 {} 页抓取失败: {}", page, exc)
            break
        if not ids:
            logger.info("列表第 {} 页无数据，停止翻页", page)
            break
        doc_ids.extend(ids)
        logger.info("列表第 {} 页获得 {} 条，累计 {}", page, len(ids), len(doc_ids))
        await asyncio.sleep(0.5)
    return doc_ids


async def _fetch_one(doc_id: int, download_delay: float) -> dict[str, Any] | None:
    """抓单个详情，返回 {'doc_id','snapshot'} 或 None。"""
    from scrapling.fetchers import AsyncFetcher

    try:
        resp = await AsyncFetcher.get(
            build_detail_url(doc_id),
            stealthy_headers=True,
            timeout=30,
            headers=_DETAIL_HEADERS,
        )
        body = resp.body
        snapshot = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        return {"doc_id": doc_id, "snapshot": snapshot}
    except Exception as exc:
        logger.error("详情 doc_id={} 抓取失败: {}", doc_id, exc)
        return None
    finally:
        if download_delay > 0:
            await asyncio.sleep(download_delay)


async def fetch_snapshots(
    doc_ids: list[int],
    concurrency: int = 5,
    download_delay: float = 0.5,
) -> list[dict[str, Any]]:
    """并发抓取详情快照。"""
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(doc_id: int) -> dict[str, Any] | None:
        async with sem:
            return await _fetch_one(doc_id, download_delay)

    results = await asyncio.gather(*(_guarded(d) for d in doc_ids))
    return [r for r in results if r is not None]


async def run_crawl(
    item_id: int = 4110,
    pages: int = 5,
    concurrency: int = 5,
    download_delay: float = 0.5,
) -> dict[str, Any]:
    """完整编排：列表发现 → 过滤 → 详情抓取 → 写入。"""
    from scrapling.fetchers import AsyncStealthySession

    await init_table()

    # 阶段 1：列表发现
    async with AsyncStealthySession(headless=True) as session:
        doc_ids = await discover_doc_ids(session, item_id, pages)
    logger.info("共发现 {} 个 docId", len(doc_ids))
    if not doc_ids:
        return {"discovered": 0, "pending": 0, "stored": 0}

    # 阶段 2：过滤已存在
    async with SnapshotSession() as db:
        repo = SnapshotRepo(db)
        existing = await repo.existing_doc_ids(set(doc_ids))
    pending = filter_pending(doc_ids, existing)
    logger.info("待抓取 {} 个（已存在 {} 个）", len(pending), len(existing))
    if not pending:
        return {"discovered": len(doc_ids), "pending": 0, "stored": 0}

    # 阶段 3：详情抓取
    snapshots = await fetch_snapshots(pending, concurrency, download_delay)
    logger.info("成功抓取 {} / {} 个详情", len(snapshots), len(pending))

    # 阶段 4：写入
    async with SnapshotSession() as db:
        repo = SnapshotRepo(db)
        stored = await repo.insert_many(snapshots)
    logger.info("写入 web_snapshot {} 行", stored)
    return {
        "discovered": len(doc_ids),
        "pending": len(pending),
        "stored": stored,
    }
