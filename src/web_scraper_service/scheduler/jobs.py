"""Job lifecycle management."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loguru import logger

from web_scraper_service.config import settings
from web_scraper_service.scheduler.engine import crawl_task


async def create_job(spider_id: uuid.UUID, spider_name: str, trigger_type: str = "manual") -> dict[str, Any]:
    """Create a new job record and dispatch it to Celery."""
    job_id = str(uuid.uuid4())
    job_data = {
        "id": job_id,
        "spider_id": spider_id,
        "spider_name": spider_name,
        "status": "pending",
        "trigger_type": trigger_type,
    }
    logger.info("Creating job {jid} for spider {name}", jid=job_id, name=spider_name)
    return job_data


async def dispatch_crawl(
    spider_name: str,
    spider_config: dict[str, Any],
    job_id: str = "",
    trigger_type: str = "manual",
) -> str:
    """Dispatch a crawl task to Celery."""
    if not job_id:
        job_id = str(uuid.uuid4())
    crawl_task.delay(spider_name, spider_config, job_id)
    logger.info("Dispatched crawl: spider={name} job={jid} trigger={t}", name=spider_name, jid=job_id, t=trigger_type)
    return job_id
