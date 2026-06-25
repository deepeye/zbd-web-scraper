from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from web_scraper_service.config import settings

# Redis DB assignments:
#   0: URL dedup
#   1: Job state cache
#   2: Crawl queues
#   3: API rate limiting

_dedup: aioredis.Redis | None = None
_job_state: aioredis.Redis | None = None
_queue: aioredis.Redis | None = None
_rate_limit: aioredis.Redis | None = None


def _make_url(db: int) -> str:
    auth = f":{settings.redis_password}@" if settings.redis_password else ""
    return f"redis://{auth}{settings.redis_host}:{settings.redis_port}/{db}"


async def init_redis() -> None:
    global _dedup, _job_state, _queue, _rate_limit
    _dedup = aioredis.from_url(_make_url(0), decode_responses=True)
    _job_state = aioredis.from_url(_make_url(1), decode_responses=True)
    _queue = aioredis.from_url(_make_url(2), decode_responses=True)
    _rate_limit = aioredis.from_url(_make_url(3), decode_responses=True)


async def close_redis() -> None:
    for client in (_dedup, _job_state, _queue, _rate_limit):
        if client:
            await client.aclose()


def dedup() -> aioredis.Redis:
    assert _dedup is not None, "Redis not initialized. Call init_redis() first."
    return _dedup


def job_state() -> aioredis.Redis:
    assert _job_state is not None, "Redis not initialized. Call init_redis() first."
    return _job_state


def queue() -> aioredis.Redis:
    assert _queue is not None, "Redis not initialized. Call init_redis() first."
    return _queue


def rate_limit() -> aioredis.Redis:
    assert _rate_limit is not None, "Redis not initialized. Call init_redis() first."
    return _rate_limit


# ── URL Dedup helpers ───────────────────────────────────────

async def is_url_seen(spider_name: str, url: str) -> bool:
    return bool(await dedup().sismember(f"dedup:{spider_name}", url))


async def mark_url_seen(spider_name: str, url: str) -> None:
    await dedup().sadd(f"dedup:{spider_name}", url)


# ── Job state helpers ───────────────────────────────────────

JOB_TTL = 86400  # 24 hours


async def set_job_state(job_id: str, state: dict[str, Any]) -> None:
    await job_state().hset(f"job:{job_id}", mapping={k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in state.items()})
    await job_state().expire(f"job:{job_id}", JOB_TTL)


async def get_job_state(job_id: str) -> dict[str, str]:
    return await job_state().hgetall(f"job:{job_id}")


async def delete_job_state(job_id: str) -> None:
    await job_state().delete(f"job:{job_id}")


# ── Queue helpers ───────────────────────────────────────────

async def enqueue(spider_name: str, url: str, priority: float = 0.0) -> None:
    await queue().zadd(f"queue:{spider_name}", {url: priority})


async def dequeue(spider_name: str) -> str | None:
    result = await queue().zpopmin(f"queue:{spider_name}")
    if not result:
        return None
    return result[0][0]


async def queue_size(spider_name: str) -> int:
    return await queue().zcard(f"queue:{spider_name}")
