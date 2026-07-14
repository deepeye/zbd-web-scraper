"""Dynamic proxy pool with periodic refresh from HTTP API.

Module-level singleton that fetches a proxy list from a remote URL,
refreshes every 10 minutes, and provides round-robin rotation with
failure marking.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

import httpx
from loguru import logger

from web_scraper_service.config import settings

_REFRESH_INTERVAL = 600  # 10 minutes


class DynamicProxyPool:
    """Fetches and rotates proxies from a remote HTTP endpoint."""

    def __init__(self) -> None:
        self._proxies: list[str] = []
        self._cycle: itertools.cycle[str] | None = None
        self._failed: set[str] = set()
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[Any] | None = None

    async def _fetch(self) -> list[str]:
        """Fetch proxy list from the configured URL. Returns server strings."""
        url = settings.proxy_pool_url
        if not url:
            logger.warning("DynamicProxyPool: proxy_pool_url not configured")
            return []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            logger.error("DynamicProxyPool: fetch failed: {}", exc)
            return []

        if payload.get("code") != "SUCCESS":
            logger.error("DynamicProxyPool: API returned code={}", payload.get("code"))
            return []

        ips = (payload.get("data") or {}).get("ips") or []
        servers = [ip["server"] for ip in ips if isinstance(ip, dict) and ip.get("server")]
        if servers:
            logger.info("DynamicProxyPool: fetched {} proxies", len(servers))
        else:
            logger.warning("DynamicProxyPool: no proxies in response")
        return servers

    async def _refresh(self) -> None:
        """Fetch new proxy list and reset cycle + failed set."""
        servers = await self._fetch()
        async with self._lock:
            if servers:
                self._proxies = servers
                self._cycle = itertools.cycle(self._proxies)
                self._failed.clear()
                logger.info("DynamicProxyPool: refreshed, {} proxies available", len(self._proxies))
            elif not self._proxies:
                logger.warning("DynamicProxyPool: initial fetch empty, no proxies available")

    async def _refresh_loop(self) -> None:
        """Background loop: refresh every _REFRESH_INTERVAL seconds."""
        while True:
            await asyncio.sleep(_REFRESH_INTERVAL)
            await self._refresh()

    async def start(self) -> None:
        """Initial fetch + start background refresh task."""
        await self._refresh()
        if self._refresh_task is None:
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Cancel background refresh task."""
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None

    def get_next(self) -> str | None:
        """Return the next available proxy (round-robin), skipping failed ones."""
        if not self._proxies or self._cycle is None:
            return None
        for _ in range(len(self._proxies)):
            proxy = next(self._cycle)
            if proxy not in self._failed:
                return proxy
        return None

    def mark_failed(self, proxy: str) -> None:
        """Mark a proxy as failed so it's skipped in future rotations."""
        self._failed.add(proxy)
        available = len(self._proxies) - len(self._failed)
        logger.warning(
            "DynamicProxyPool: proxy {} marked failed, {}/{} available",
            proxy, max(available, 0), len(self._proxies),
        )

    @property
    def available_count(self) -> int:
        return max(len(self._proxies) - len(self._failed), 0)


# Module-level singleton
_pool: DynamicProxyPool | None = None


def get_proxy_pool() -> DynamicProxyPool:
    """Get or create the module-level DynamicProxyPool singleton."""
    global _pool
    if _pool is None:
        _pool = DynamicProxyPool()
    return _pool