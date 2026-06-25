"""Async HTTP fetcher wrapping Scrapling Fetcher with retry, rate-limiting, and proxy injection."""

from __future__ import annotations

import asyncio
import random
from typing import Any

from loguru import logger
from scrapling.engines.toolbelt.custom import Response
from scrapling.fetchers import AsyncFetcher
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from web_scraper_service.config import settings
from web_scraper_service.fetchers.proxy import get_proxy


class HttpFetcher:
    """Lightweight HTTP fetcher with retry, rate limiting, and proxy support."""

    def __init__(
        self,
        retry_times: int | None = None,
        retry_delay: float | None = None,
        timeout: int | None = None,
        proxy_enabled: bool = False,
        stealthy: bool = False,
        impersonate: str = "chrome",
    ) -> None:
        self.retry_times = retry_times or settings.default_retry_times
        self.retry_delay = retry_delay or settings.default_retry_delay
        self.timeout = timeout or settings.default_timeout
        self.proxy_enabled = proxy_enabled
        self.stealthy = stealthy
        self.impersonate = impersonate
        self._semaphore = asyncio.Semaphore(settings.default_concurrency)

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def _do_get(self, url: str, **kwargs: Any) -> Response:
        proxy = get_proxy() if self.proxy_enabled else None
        return await AsyncFetcher.get(
            url,
            stealthy_headers=self.stealthy,
            proxy=proxy,
            impersonate=self.impersonate,
            timeout=self.timeout,
            **kwargs,
        )

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def _do_post(self, url: str, data: dict | None = None, json: dict | None = None, **kwargs: Any) -> Response:
        proxy = get_proxy() if self.proxy_enabled else None
        return await AsyncFetcher.post(
            url,
            data=data,
            json=json,
            stealthy_headers=self.stealthy,
            proxy=proxy,
            impersonate=self.impersonate,
            timeout=self.timeout,
            **kwargs,
        )

    async def get(self, url: str, **kwargs: Any) -> Response:
        async with self._semaphore:
            logger.debug("GET {url}", url=url)
            return await self._do_get(url, **kwargs)

    async def post(self, url: str, data: dict | None = None, json: dict | None = None, **kwargs: Any) -> Response:
        async with self._semaphore:
            logger.debug("POST {url}", url=url)
            return await self._do_post(url, data=data, json=json, **kwargs)
