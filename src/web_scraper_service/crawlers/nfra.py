"""nfra.gov.cn 文档采集：纯逻辑 + 异步编排。

纯逻辑（build_*/parse_doc_rows）可单测；异步编排（discover_doc_rows /
run_crawl）涉及网络与浏览器，靠手动 smoke 验收。结果写入独立库
zbd_crawler_data.djg_data（经 storage/djg_data.py）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from web_scraper_service.crawlers.nfra_extractor import extract_rows_llm
from web_scraper_service.storage.djg_data import DjgDataRepo, init_djg_table
from web_scraper_service.storage.snapshot import SnapshotSession

BASE = "https://www.nfra.gov.cn"


def build_list_url(item_id: int, page: int, page_size: int = 18) -> str:
    return (
        f"{BASE}/cn/static/data/DocInfo/SelectDocByItemIdAndChild/"
        f"data_itemId={item_id},pageIndex={page},pageSize={page_size}.json"
    )


def build_detail_html_url(doc_id: int) -> str:
    return f"{BASE}/cn/view/pages/ItemDetail.html?docId={doc_id}&itemId=4111&generaltype=0"


def build_list_html_url(item_id: int) -> str:
    return (
        f"{BASE}/cn/view/pages/ItemList.html?itemPId=923&itemId={item_id}"
        f"&itemUrl=ItemListRightList.html&itemName=zhujiguan"
    )


def parse_doc_rows(body: str | bytes) -> list[dict[str, Any]]:
    """解析列表接口响应，返回 [{'docId': int, 'docTitle': str}, ...]。"""
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
    out: list[dict[str, Any]] = []
    for row in rows:
        doc_id = row.get("docId")
        if isinstance(doc_id, int):
            out.append({"docId": doc_id, "docTitle": str(row.get("docTitle") or "")})
    return out


# ── 异步编排（网络/浏览器，靠手动 smoke 验收）──────────────

_LIST_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE}/cn/view/pages/ItemList.html",
}


async def discover_doc_rows(
    session: Any,
    item_id: int,
    pages: int,
) -> list[dict[str, Any]]:
    """用浏览器持久会话遍历列表 API，返回含标题的行。"""
    rows: list[dict[str, Any]] = []
    await session.fetch(build_list_html_url(item_id))
    for page in range(1, pages + 1):
        url = build_list_url(item_id, page)
        try:
            resp = await session.fetch(url, extra_headers=_LIST_HEADERS)
            page_rows = parse_doc_rows(resp.body)
        except Exception as exc:
            logger.warning("列表第 {} 页抓取失败: {}", page, exc)
            break
        if not page_rows:
            logger.info("列表第 {} 页无数据，停止翻页", page)
            break
        rows.extend(page_rows)
        logger.info("列表第 {} 页获得 {} 条，累计 {}", page, len(page_rows), len(rows))
        await asyncio.sleep(0.5)
    return rows


async def _fetch_detail_rows(
    session: Any,
    doc_id: int,
    doc_url: str,
    download_delay: float,
) -> list[dict[str, Any]]:
    """打开详情 HTML，LLM 抽取，返回 djg_data 行列表。"""
    try:
        resp = await session.fetch(doc_url, network_idle=True, timeout=60000)
        # 用渲染后 DOM（html_content），非原始响应体 body——抽取器选择器针对渲染 DOM 设计。
        # body 是 angular 模板壳子，分局文档的 doc_number 仅在渲染后正文/绑定元素中可见。
        html = resp.html_content or ""
        rows = await extract_rows_llm(doc_id, html, doc_url)
        return rows
    except Exception as exc:
        logger.error("详情 doc_id={} 抽取失败: {}", doc_id, exc)
        return []
    finally:
        if download_delay > 0:
            await asyncio.sleep(download_delay)


async def run_crawl(
    item_id: int = 4110,
    pages: int = 5,
    concurrency: int = 2,
    download_delay: float = 1.0,
) -> dict[str, Any]:
    """完整编排：列表发现 → 标题过滤 → 跳过已存在 → 详情抽取 → 写 djg_data。"""
    from scrapling.fetchers import AsyncDynamicSession, AsyncStealthySession

    await init_djg_table()

    # 阶段 1：列表发现（含标题）
    async with AsyncStealthySession(headless=True) as session:
        rows = await discover_doc_rows(session, item_id, pages)
    logger.info("共发现 {} 条文档", len(rows))
    if not rows:
        return {"discovered": 0, "pending": 0, "extracted_rows": 0, "stored": 0}

    # 阶段 2：标题过滤（仅任职资格类）
    qualified = [r for r in rows if "任职资格" in r["docTitle"]]
    logger.info("标题含「任职资格」 {} 条", len(qualified))
    if not qualified:
        return {"discovered": len(rows), "pending": 0, "extracted_rows": 0, "stored": 0}

    # 阶段 3：跳过已存在
    pending_ids = [r["docId"] for r in qualified]
    async with SnapshotSession() as db:
        repo = DjgDataRepo(db)
        existing = await repo.existing_doc_ids(set(pending_ids))
    pending = [r for r in qualified if r["docId"] not in existing]
    logger.info("待抓取 {} 个（已存在 {} 个）", len(pending), len(existing))
    if not pending:
        return {"discovered": len(rows), "pending": 0, "extracted_rows": 0, "stored": 0}

    # 阶段 4+5：详情抽取并逐 doc 写入（边抽边写，崩溃只丢当前未完成 doc）
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(r: dict[str, Any]) -> tuple[int, int]:
        """抽一个 doc 的行，立即写入 djg_data；返回 (extracted, stored)。"""
        async with sem:
            batch = await _fetch_detail_rows(
                session, r["docId"], build_detail_html_url(r["docId"]), download_delay
            )
        if not batch:
            return 0, 0
        async with SnapshotSession() as db:
            repo = DjgDataRepo(db)
            stored = await repo.insert_many(batch)
        logger.info("doc_id={} 抽取 {} 行，写入 {} 行", r["docId"], len(batch), stored)
        return len(batch), stored

    async with AsyncDynamicSession(headless=True) as session:
        results = await asyncio.gather(*(_guarded(r) for r in pending))
    extracted_rows = sum(r[0] for r in results)
    stored = sum(r[1] for r in results)
    logger.info("抽取行数 {}，写入 djg_data {} 行", extracted_rows, stored)
    return {
        "discovered": len(rows),
        "pending": len(pending),
        "extracted_rows": extracted_rows,
        "stored": stored,
    }
