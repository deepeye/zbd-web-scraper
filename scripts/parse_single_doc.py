"""单文档解析并入库：抓取指定 docId 的详情页，分别用资本/股权抽取器抽取，写入对应表。

用法:
    uv run python scripts/parse_single_doc.py --doc-id 1260947
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from loguru import logger

from web_scraper_service.core.logging import setup_logging
from web_scraper_service.crawlers.nfra import build_detail_html_url
from web_scraper_service.crawlers.nfra_extractor import doc_title
from web_scraper_service.crawlers.nfra_capital_extractor import (
    extract_rows_llm as extract_capital,
    is_capital_candidate,
)
from web_scraper_service.crawlers.nfra_equity_extractor import (
    extract_rows_llm as extract_equity,
    is_equity_candidate,
)
from web_scraper_service.storage.capital_change_data import (
    CapitalChangeDataRepo,
    init_capital_change_table,
)
from web_scraper_service.storage.equity_change_data import (
    EquityChangeDataRepo,
    init_equity_change_table,
)
from web_scraper_service.storage.snapshot import SnapshotSession


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="解析单个 nfra 文档并入库（资本+股权）")
    parser.add_argument("--doc-id", type=int, required=True, help="文档 docId")
    parser.add_argument(
        "--json-out",
        action="store_true",
        help="向 stdout 打印单行 JSON 结果",
    )
    return parser.parse_args()


async def parse_single(doc_id: int) -> dict:
    """抓取详情页，资本+股权抽取，分别写入 capital_change_data / equity_change_data。"""
    from scrapling.fetchers import AsyncDynamicSession

    await init_capital_change_table()
    await init_equity_change_table()

    doc_url = build_detail_html_url(doc_id)
    logger.info("开始抓取 doc_id={} url={}", doc_id, doc_url)

    async with AsyncDynamicSession(headless=True) as session:
        resp = await session.fetch(doc_url, network_idle=True, timeout=60000)
        html = resp.html_content or ""

    if not html:
        logger.warning("doc_id={} 页面为空", doc_id)
        return {"doc_id": doc_id, "capital": {"extracted": 0, "stored": 0}, "equity": {"extracted": 0, "stored": 0}}

    title = doc_title(html)
    logger.info("doc_id={} 标题: {}", doc_id, title)

    summary: dict = {"doc_id": doc_id, "title": title, "capital": {"extracted": 0, "stored": 0}, "equity": {"extracted": 0, "stored": 0}}

    # ── 资本变更 ──
    if is_capital_candidate(title):
        capital_rows = await extract_capital(doc_id, html, doc_url)
        logger.info("doc_id={} 资本抽取 {} 行", doc_id, len(capital_rows))
        summary["capital"]["extracted"] = len(capital_rows)
        if capital_rows:
            async with SnapshotSession() as db:
                repo = CapitalChangeDataRepo(db)
                stored = await repo.insert_many(capital_rows)
            summary["capital"]["stored"] = stored
            logger.info("doc_id={} 资本写入 {} 行", doc_id, stored)
    else:
        logger.info("doc_id={} 标题不含「注册资本」或「开业」，跳过资本抽取", doc_id)

    # ── 股权变更 ──
    if is_equity_candidate(title):
        equity_rows = await extract_equity(doc_id, html, doc_url)
        logger.info("doc_id={} 股权抽取 {} 行", doc_id, len(equity_rows))
        summary["equity"]["extracted"] = len(equity_rows)
        if equity_rows:
            async with SnapshotSession() as db:
                repo = EquityChangeDataRepo(db)
                stored = await repo.insert_many(equity_rows)
            summary["equity"]["stored"] = stored
            logger.info("doc_id={} 股权写入 {} 行", doc_id, stored)
    else:
        logger.info("doc_id={} 标题不含「股权」或「开业」，跳过股权抽取", doc_id)

    return summary


def main() -> int:
    args = parse_args()
    setup_logging()
    result = asyncio.run(parse_single(args.doc_id))
    if args.json_out:
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    else:
        print(f"\ndoc_id={result['doc_id']} 标题: {result['title']}")
        cap = result["capital"]
        eq = result["equity"]
        print(f"  资本变更: 抽取 {cap['extracted']} 行, 写入 {cap['stored']} 行")
        print(f"  股权变更: 抽取 {eq['extracted']} 行, 写入 {eq['stored']} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())