"""nfra.gov.cn 文档快照采集 CLI 入口。

用法:
    python scripts/crawl_nfra.py --pages 5 --item-id 4110
    make crawl-nfra
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from web_scraper_service.core.logging import setup_logging
from web_scraper_service.crawlers.nfra import run_crawl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 nfra.gov.cn 文档快照")
    parser.add_argument("--pages", type=int, default=5, help="采集最新页数（默认 5）")
    parser.add_argument("--item-id", type=int, default=4110, help="栏目 itemId（默认 4110）")
    parser.add_argument("--concurrency", type=int, default=5, help="详情并发数（默认 5）")
    parser.add_argument(
        "--download-delay", type=float, default=0.5, help="详情请求间隔秒（默认 0.5）"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging()
    logger.info(
        "启动 nfra 采集: itemId={} pages={} concurrency={}",
        args.item_id, args.pages, args.concurrency,
    )
    stats = asyncio.run(
        run_crawl(
            item_id=args.item_id,
            pages=args.pages,
            concurrency=args.concurrency,
            download_delay=args.download_delay,
        )
    )
    logger.info("采集完成: {}", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
