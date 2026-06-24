"""URL and content deduplication using Redis Set or BloomFilter."""

from __future__ import annotations

import hashlib
from typing import Any

from loguru import logger

from web_scraper_service.storage.redis import is_url_seen, mark_url_seen


async def is_duplicate_url(spider_name: str, url: str) -> bool:
    """Check if URL has been seen (Redis Set)."""
    return await is_url_seen(spider_name, url)


async def mark_url_crawled(spider_name: str, url: str) -> None:
    """Mark URL as crawled in Redis."""
    await mark_url_seen(spider_name, url)


def content_hash(data: dict[str, Any]) -> str:
    """Compute SHA-256 hash of item data for change detection."""
    serialized = str(sorted(data.items())).encode()
    return hashlib.sha256(serialized).hexdigest()


async def is_duplicate_content(spider_name: str, data: dict[str, Any]) -> tuple[bool, str]:
    """Check if content has changed using hash comparison.

    Returns (is_duplicate, hash_str).
    """
    h = content_hash(data)
    seen = await is_url_seen(f"content:{spider_name}", h)
    if seen:
        return True, h
    await mark_url_seen(f"content:{spider_name}", h)
    return False, h
