"""File-based proxy pool with on-exhaustion refresh.

Fetches a batch of proxies from a remote HTTP API, caches them to a local
JSON file (with deadline), and rotates through them.  When all proxies are
exhausted or the deadline has passed, waits 5 minutes then re-fetches.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from web_scraper_service.config import BASE_DIR, settings

_CACHE_FILE = BASE_DIR / "proxy_cache.json"
_REFRESH_COOLDOWN = 300  # 5 minutes


class DynamicProxyPool:
    """Fetches a batch of proxies, caches to file, rotates with exhaustion refresh."""

    def __init__(self) -> None:
        self._proxies: list[str] = []
        self._deadline: str | None = None  # "YYYY-MM-DD HH:MM:SS"
        self._failed: set[str] = set()
        self._cache_file = Path(settings.proxy_cache_file) if settings.proxy_cache_file else _CACHE_FILE
        self._refreshing = asyncio.Lock()

    # ── API fetch ────────────────────────────────────────────

    async def _fetch_all(self) -> tuple[list[str], str | None]:
        """Fetch proxies from the configured URL. Returns (servers, deadline).

        On a 400 Bad Request (typical qg.net rate-limit response) the fetch
        loops with a 5-minute backoff until it succeeds.
        """
        url = settings.proxy_pool_url
        if not url:
            logger.warning("DynamicProxyPool: proxy_pool_url not configured")
            return [], None

        while True:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    payload = resp.json()
            except httpx.HTTPStatusError as exc:
                logger.error("DynamicProxyPool: fetch failed: {}", exc)
                if exc.response.status_code == 400:
                    logger.info(
                        "DynamicProxyPool: got 400 Bad Request, retrying in {}s",
                        _REFRESH_COOLDOWN,
                    )
                    await asyncio.sleep(_REFRESH_COOLDOWN)
                    continue
                return [], None
            except Exception as exc:
                logger.error("DynamicProxyPool: fetch failed: {}", exc)
                return [], None

            if payload.get("code") != "SUCCESS":
                logger.error("DynamicProxyPool: API returned code={}", payload.get("code"))
                return [], None

            ips = (payload.get("data") or {}).get("ips") or []
            servers = [ip["server"] for ip in ips if isinstance(ip, dict) and ip.get("server")]
            deadline = ips[0].get("deadline") if ips else None
            if servers:
                logger.info("DynamicProxyPool: fetched {} proxies, deadline={}", len(servers), deadline)
            else:
                logger.warning("DynamicProxyPool: no proxies in response")
            return servers, deadline

    # ── File cache ───────────────────────────────────────────

    def _load_cache(self) -> bool:
        """Load proxies from cache file. Returns False if missing / expired / empty."""
        if not self._cache_file.exists():
            return False
        try:
            data = json.loads(self._cache_file.read_text())
            proxies: list[str] = data.get("proxies", [])
            deadline_str: str | None = data.get("deadline")
            if not proxies:
                return False
            if deadline_str:
                try:
                    dl = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() > dl:
                        logger.info("Proxy cache expired (deadline={})", deadline_str)
                        return False
                except ValueError:
                    pass
            self._proxies = proxies
            self._deadline = deadline_str
            self._failed = set(data.get("failed") or [])
            logger.info("Loaded {} proxies from cache, deadline={}, failed={}",
                        len(proxies), deadline_str, len(self._failed))
            return True
        except Exception as exc:
            logger.warning("Failed to load proxy cache: {}", exc)
            return False

    def _save_cache(self) -> None:
        """Write current proxies, deadline, and failed set to the cache file."""
        data = {"proxies": self._proxies, "deadline": self._deadline, "failed": list(self._failed)}
        self._cache_file.write_text(json.dumps(data, ensure_ascii=False))
        logger.info("Saved {} proxies ({} failed) to {}", len(self._proxies), len(self._failed), self._cache_file)

    # ── Refresh ──────────────────────────────────────────────

    async def _refresh(self) -> None:
        """Fetch all proxies and overwrite the cache file."""
        servers, deadline = await self._fetch_all()
        if servers:
            self._proxies = servers
            self._deadline = deadline
            self._failed.clear()
            self._save_cache()
        elif not self._proxies:
            logger.warning("DynamicProxyPool: initial fetch returned no proxies")

    # ── Public API ───────────────────────────────────────────

    async def start(self) -> None:
        """Load proxies from cache file, or fetch fresh if cache is invalid."""
        if not self._load_cache():
            await self._refresh()

    async def stop(self) -> None:
        """No-op: the file cache persists across sessions."""
        pass

    def get_next(self) -> str | None:
        """Return the next available (non-failed) proxy, or None if exhausted."""
        available = [p for p in self._proxies if p not in self._failed]
        return available[0] if available else None

    def mark_failed(self, proxy: str) -> None:
        """Mark a proxy as failed and persist to cache file."""
        self._failed.add(proxy)
        remaining = len(self._proxies) - len(self._failed)
        logger.warning(
            "DynamicProxyPool: {} marked failed, {}/{} remaining",
            proxy, max(remaining, 0), len(self._proxies),
        )
        self._save_cache()

    def is_exhausted(self) -> bool:
        """True when all proxies have been marked failed."""
        return len(self._proxies) > 0 and len(self._failed) >= len(self._proxies)

    async def wait_and_refresh(self) -> None:
        """Wait 5 minutes then re-fetch all proxies from the API."""
        async with self._refreshing:
            # Double-check: another page error may have already refreshed
            if not self.is_exhausted():
                return
            logger.info("All proxies exhausted, waiting {}s before refresh", _REFRESH_COOLDOWN)
            await asyncio.sleep(_REFRESH_COOLDOWN)
            await self._refresh()

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