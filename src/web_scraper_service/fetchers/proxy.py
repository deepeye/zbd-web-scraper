"""Proxy pool management with rotation and health checking."""

from __future__ import annotations

import itertools
import random
from typing import Any

from loguru import logger

from web_scraper_service.config import settings

_proxy_cycle: itertools.cycle[str] | None = None
_proxy_list: list[str] = []
_failed_proxies: set[str] = set()


def init_proxies() -> None:
    global _proxy_cycle, _proxy_list
    _proxy_list = list(settings.proxies)
    if _proxy_list:
        _proxy_cycle = itertools.cycle(_proxy_list)
        logger.info("Proxy pool initialized with {count} proxies", count=len(_proxy_list))
    else:
        logger.info("No proxies configured")


def get_proxy() -> str | None:
    """Get the next proxy from the rotation pool."""
    if not _proxy_list or _proxy_cycle is None:
        return None

    if settings.proxy_rotation_strategy == "random":
        available = [p for p in _proxy_list if p not in _failed_proxies]
        return random.choice(available) if available else None

    # Round-robin
    for _ in range(len(_proxy_list)):
        proxy = next(_proxy_cycle)
        if proxy not in _failed_proxies:
            return proxy
    return None


def mark_proxy_failed(proxy: str) -> None:
    """Mark a proxy as failed so it's skipped in future rotations."""
    _failed_proxies.add(proxy)
    logger.warning("Proxy marked failed: {proxy}, remaining={count}", proxy=proxy, count=len(_proxy_list) - len(_failed_proxies))


def reset_failed_proxies() -> None:
    """Reset the failed proxy set (e.g., on schedule)."""
    _failed_proxies.clear()
    logger.info("Failed proxy set reset")


def proxy_stats() -> dict[str, Any]:
    return {
        "total": len(_proxy_list),
        "failed": len(_failed_proxies),
        "available": len(_proxy_list) - len(_failed_proxies),
    }
