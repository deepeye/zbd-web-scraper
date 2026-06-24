from __future__ import annotations

import abc
import asyncio
from typing import Any

from loguru import logger
from scrapling.engines.toolbelt.custom import Response
from scrapling.fetchers import AsyncFetcher

from web_scraper_service.config import settings
from web_scraper_service.core.logging import job_id_var, spider_name_var
from web_scraper_service.core.metrics import CrawlMetrics


class BaseSpider(abc.ABC):
    """Abstract base class for all spiders.

    Subclass must implement:
        - parse(response, **kwargs) -> AsyncGenerator[dict, None]
    Optionally override:
        - fetch(url) -> Adaptor
        - pipeline(item) -> dict | None
    """

    name: str = ""
    start_urls: list[str] = []
    concurrency: int = settings.default_concurrency
    download_delay: float = settings.default_download_delay
    use_playwright: bool = False
    use_camoufox: bool = False
    use_stealthy: bool = False
    retry_times: int = settings.default_retry_times
    proxy_enabled: bool = False
    adaptive: bool = settings.scrapling_adaptive

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self._metrics = CrawlMetrics(spider_name=self.name)
        self._semaphore = asyncio.Semaphore(self.concurrency)

    async def fetch(self, url: str) -> Response:
        """Fetch a single URL. Override to use Playwright/Camoufox/Stealthy."""
        if self.use_playwright:
            from scrapling.fetchers import StealthyFetcher

            StealthyFetcher.adaptive = self.adaptive
            return await StealthyFetcher.async_fetch(
                url,
                headless=settings.playwright_headless,
                network_idle=True,
                timeout=settings.default_timeout * 1000,
            )
        if self.use_camoufox:
            from scrapling.fetchers import StealthyFetcher

            StealthyFetcher.adaptive = self.adaptive
            return await StealthyFetcher.async_fetch(
                url,
                headless=settings.camoufox_headless,
                network_idle=True,
                timeout=settings.default_timeout * 1000,
            )
        return await AsyncFetcher.get(
            url,
            stealthy_headers=self.use_stealthy,
            timeout=settings.default_timeout,
        )

    @abc.abstractmethod
    async def parse(self, response: Response, **kwargs: Any) -> Any:
        """Parse a fetched page and yield structured items."""
        ...

    async def pipeline(self, item: dict[str, Any]) -> dict[str, Any] | None:
        """Post-process an item before storage. Return None to drop."""
        return item

    async def on_error(self, url: str, error: Exception) -> None:
        """Called when a URL fetch or parse fails."""
        logger.error("Spider={name} URL={url} error={error}", name=self.name, url=url, error=str(error))
        self._metrics.record_request(success=False, url=url)

    async def _process_url(self, url: str) -> list[dict[str, Any]]:
        """Fetch, parse, and pipeline a single URL with semaphore control."""
        async with self._semaphore:
            try:
                response = await self.fetch(url)
                self._metrics.record_request(success=True, url=url)
                items: list[dict[str, Any]] = []
                async for raw_item in self.parse(response, url=url):
                    processed = await self.pipeline(raw_item) if isinstance(raw_item, dict) else raw_item
                    if processed is not None:
                        items.append(processed)
                        self._metrics.record_item(stored=True)
                return items
            except Exception as exc:
                await self.on_error(url, exc)
                return []
            finally:
                if self.download_delay > 0:
                    await asyncio.sleep(self.download_delay)

    async def run(self, job_id: str = "") -> CrawlMetrics:
        """Execute the spider across all start_urls concurrently."""
        job_id_var.set(job_id)
        spider_name_var.set(self.name)
        self._metrics.job_id = job_id
        logger.info("Spider {name} starting, urls={count}", name=self.name, count=len(self.start_urls))

        tasks = [self._process_url(url) for url in self.start_urls]
        results = await asyncio.gather(*tasks)

        all_items = [item for batch in results for item in batch]
        logger.info(
            "Spider {name} done, items={count}",
            name=self.name,
            count=len(all_items),
        )
        self._metrics.finish()
        return self._metrics
