"""Async browser fetcher wrapping Scrapling StealthyFetcher for SPA / JS-rendered pages."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from scrapling.engines.toolbelt.custom import Response
from scrapling.fetchers import StealthyFetcher
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from web_scraper_service.config import settings
from web_scraper_service.fetchers.proxy import get_proxy


class BrowserFetcher:
    """Playwright-driven fetcher for SPA/JS-rendered pages with stealth capabilities."""

    def __init__(
        self,
        headless: bool = True,
        network_idle: bool = True,
        timeout: int | None = None,
        proxy_enabled: bool = False,
        adaptive: bool = True,
        solve_cloudflare: bool = False,
    ) -> None:
        self.headless = headless
        self.network_idle = network_idle
        self.timeout = (timeout or settings.default_timeout) * 1000  # ms
        self.proxy_enabled = proxy_enabled
        self.adaptive = adaptive
        self.solve_cloudflare = solve_cloudflare
        self._semaphore = asyncio.Semaphore(settings.default_concurrency)

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def _do_fetch(self, url: str, **kwargs: Any) -> Response:
        StealthyFetcher.adaptive = self.adaptive
        proxy = get_proxy() if self.proxy_enabled else None
        return await StealthyFetcher.async_fetch(
            url,
            headless=self.headless,
            network_idle=self.network_idle,
            timeout=self.timeout,
            proxy=proxy,
            solve_cloudflare=self.solve_cloudflare,
            **kwargs,
        )

    async def fetch(self, url: str, **kwargs: Any) -> Response:
        async with self._semaphore:
            logger.debug("BROWSER FETCH {url}", url=url)
            return await self._do_fetch(url, **kwargs)

    async def fetch_with_wait(self, url: str, wait_selector: str, wait_selector_state: str = "attached", **kwargs: Any) -> Response:
        """Fetch and wait for a specific CSS selector to appear."""
        async with self._semaphore:
            logger.debug("BROWSER FETCH+WAIT {url} selector={sel}", url=url, sel=wait_selector)
            return await self._do_fetch(
                url,
                wait_selector=wait_selector,
                wait_selector_state=wait_selector_state,
                **kwargs,
            )
