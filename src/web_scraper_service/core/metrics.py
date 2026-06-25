from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from web_scraper_service.core.logging import job_id_var, spider_name_var


@dataclass
class CrawlMetrics:
    spider_name: str = ""
    job_id: str = ""
    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    end_time: datetime | None = None
    requests_total: int = 0
    requests_success: int = 0
    requests_failed: int = 0
    items_scraped: int = 0
    items_stored: int = 0
    items_deduped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def record_request(self, success: bool, url: str = "") -> None:
        self.requests_total += 1
        if success:
            self.requests_success += 1
        else:
            self.requests_failed += 1
            self.errors.append({"url": url, "time": datetime.now(UTC).isoformat()})

    def record_item(self, stored: bool = True, deduped: bool = False) -> None:
        self.items_scraped += 1
        if stored:
            self.items_stored += 1
        if deduped:
            self.items_deduped += 1

    def finish(self) -> dict[str, Any]:
        self.end_time = datetime.now(UTC)
        elapsed = (self.end_time - self.start_time).total_seconds()
        summary = {
            "spider_name": self.spider_name,
            "job_id": self.job_id,
            "elapsed_seconds": elapsed,
            "requests_total": self.requests_total,
            "requests_success": self.requests_success,
            "requests_failed": self.requests_failed,
            "items_scraped": self.items_scraped,
            "items_stored": self.items_stored,
            "items_deduped": self.items_deduped,
            "requests_per_second": self.requests_total / elapsed if elapsed > 0 else 0,
        }
        logger.info("Crawl finished: {summary}", summary=summary)
        return summary
