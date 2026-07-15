"""Debug why a capital-extraction doc returns 0 rows.

Dumps every intermediate stage so you can see WHERE extraction drops the data:
  rendered-DOM title / doc_number / prose(length+preview) -> raw LLM response
  -> raw JSON rows -> parse_llm_rows filter outcome (with per-row drop reason).

Replicates the production fetch exactly (disable_resources=True, optional proxy).

Usage (server, /app):
    python scripts/debug_capital_extraction.py --doc-id 1264551 --doc-id 1264588
    python scripts/debug_capital_extraction.py --doc-id 1264551 --server 123.189.61.60:14398
"""

from __future__ import annotations

import argparse
import asyncio
import json

from loguru import logger

from web_scraper_service.core.logging import setup_logging
from web_scraper_service.crawlers.nfra import _build_proxy_url, build_detail_html_url
from web_scraper_service.crawlers.nfra_capital_extractor import (
    _BRANCH_WORDS,
    _FIELDS,
    _call_llm,
    _is_branch_opening,
    is_capital_candidate,
    parse_llm_rows,
)
from web_scraper_service.crawlers.nfra_extractor import (
    clean_prose,
    doc_number,
    doc_title,
    issuing_authority,
    publish_date,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Debug capital extraction 0-rows")
    p.add_argument("--doc-id", type=int, action="append", required=True, help="docId (repeatable)")
    p.add_argument("--server", type=str, default=None, help="proxy 'ip:port' to match production")
    return p.parse_args()


def _why_dropped(row: dict) -> str:
    ct = str(row.get("change_type", "")).strip()
    inst = str(row.get("institution_name", "")).strip()
    if ct not in {"变更注册资本", "机构成立"}:
        return f"change_type={ct!r} (not 变更注册资本/机构成立)"
    if not inst:
        return "institution_name empty"
    if _is_branch_opening(row):
        return f"branch opening (institution contains {_BRANCH_WORDS}): {inst}"
    return ""


async def debug_one(doc_id: int, proxy_url: str | None) -> None:
    from scrapling.fetchers import AsyncDynamicSession

    url = build_detail_html_url(doc_id)
    logger.info("=" * 70)
    logger.info("doc_id={} url={}", doc_id, url)
    logger.info("proxy={}", proxy_url or "NONE (direct)")

    async with AsyncDynamicSession(headless=True, proxy=proxy_url, max_pages=1) as session:
        resp = await session.fetch(
            url, network_idle=True, timeout=60000, disable_resources=True
        )
        html = resp.html_content or ""

    logger.info("fetched: status={} html_len={}", resp.status, len(html))

    title = doc_title(html)
    number = doc_number(html)
    prose = clean_prose(html)
    authority = issuing_authority(title)
    logger.info("doc_title       = {!r}", title)
    logger.info("doc_number      = {!r}", number)
    logger.info("issuing_authority={!r}", authority)
    logger.info("publish_date    = {}", publish_date(html))
    logger.info("prose_len       = {}", len(prose))
    logger.info("prose_preview   = {!r}", prose[:1000])

    if not prose:
        logger.warning(">>> prose EMPTY — #wenzhang-content not in rendered DOM")
        logger.warning("    extraction has no prose input to send the LLM.")
        return

    logger.info("-" * 40)
    logger.info("is_capital_candidate(title)={} (list-title filter would {} this doc)",
                is_capital_candidate(title), "PASS" if is_capital_candidate(title) else "SKIP")

    logger.info("-" * 40)
    logger.info("calling LLM…")
    try:
        raw = await _call_llm(title, number, prose)
    except Exception as exc:
        logger.error(">>> LLM call FAILED: {}", exc)
        return
    logger.info("raw LLM response:\n{}", raw)

    logger.info("-" * 40)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(">>> LLM response is not valid JSON: {}", exc)
        return
    raw_rows = payload.get("rows") if isinstance(payload, dict) else None
    raw_count = len(raw_rows) if isinstance(raw_rows, list) else "n/a"
    logger.info("raw rows count = {} (type={})", raw_count, type(raw_rows).__name__)
    if isinstance(raw_rows, list):
        for i, r in enumerate(raw_rows):
            norm = {f: str(r.get(f) or "").strip() for f in _FIELDS}
            why = _why_dropped(r)
            tag = "KEEP" if not why else f"DROP: {why}"
            logger.info("  raw[{}]: {} -> {}", i, norm, tag)

    logger.info("-" * 40)
    parsed = parse_llm_rows(raw)
    logger.info("parse_llm_rows kept = {} row(s)", len(parsed))
    for r in parsed:
        logger.info("  kept: {}", r)


async def main() -> int:
    args = parse_args()
    setup_logging()
    proxy_url = _build_proxy_url(args.server) if args.server else None
    for doc_id in args.doc_id:
        await debug_one(doc_id, proxy_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
