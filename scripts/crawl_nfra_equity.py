"""nfra.gov.cn 股权变更/开业股东数据采集 CLI 入口。

用法:
    python scripts/crawl_nfra_equity.py --pages 5
    python scripts/crawl_nfra_equity.py --item-id 4291 --pages 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from loguru import logger

from web_scraper_service.core.logging import setup_logging
from web_scraper_service.crawlers.nfra_equity import run_crawl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 nfra.gov.cn 股权变更/开业股东数据")
    parser.add_argument("--pages", type=int, default=5, help="采集最新页数（默认 5）")
    parser.add_argument("--item-id", type=int, default=None, help="栏目 itemId；不传则采集 4110 和 4291")
    parser.add_argument("--concurrency", type=int, default=2, help="详情并发数（默认 2，浏览器+LLM）")
    parser.add_argument("--download-delay", type=float, default=1.0, help="详情请求间隔秒（默认 1.0）")
    parser.add_argument(
        "--json-out",
        action="store_true",
        help="采集完成后向 stdout 打印单行 JSON 统计（供 Celery 子进程任务解析）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging()
    logger.info(
        "启动 nfra 股权变更/开业股东采集: itemId={} pages={} concurrency={}",
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
    if args.json_out:
        # 单行 JSON，供父进程解析；必须是 stdout 最后一行
        print(json.dumps(stats, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
