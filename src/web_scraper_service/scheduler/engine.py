"""Scheduling engine: APScheduler for in-process + Celery for distributed tasks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from celery import Celery
from celery.schedules import crontab
from loguru import logger

from web_scraper_service.config import settings
from web_scraper_service.spiders.registry import create_spider

# ── Celery app ──────────────────────────────────────────────

celery_app = Celery(
    "web_scraper_service",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
)


# ── Celery tasks ────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def crawl_task(self: Any, spider_name: str, spider_config: dict[str, Any], job_id: str = "") -> dict[str, Any]:
    """Celery task to run a spider."""
    try:
        spider = create_spider(spider_name, **spider_config)
        # Run the async spider in a new event loop
        loop = asyncio.new_event_loop()
        try:
            metrics = loop.run_until_complete(spider.run(job_id=job_id or str(uuid.uuid4())))
        finally:
            loop.close()
        return metrics.finish()
    except Exception as exc:
        logger.error("Crawl task failed: spider={name} error={err}", name=spider_name, err=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def clean_task(self: Any, job_id: str, spider_name: str) -> dict[str, Any]:
    """Celery task for post-crawl data cleaning."""
    try:
        logger.info("Running clean task for job={jid} spider={name}", jid=job_id, name=spider_name)
        # Clean logic is invoked via the pipeline system
        return {"job_id": job_id, "status": "cleaned"}
    except Exception as exc:
        logger.error("Clean task failed: job={jid} error={err}", jid=job_id, err=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def nfra_crawl_task(self: Any, item_id: int, pages: int) -> dict[str, Any]:
    """Run the nfra crawler in a subprocess (isolated event loop per crawl).

    Uses subprocess to avoid asyncio loop-binding issues with module-level
    async engines (snapshot_engine) across per-task loops in the Celery worker.
    The subprocess runs scripts/crawl_nfra.py --json-out; the last stdout line
    is a JSON stats dict.
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    cmd = [
        sys.executable,
        str(repo_root / "scripts" / "crawl_nfra.py"),
        "--item-id", str(item_id),
        "--pages", str(pages),
        "--json-out",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=3600,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            raise RuntimeError(f"crawl_nfra.py exited {proc.returncode}: {tail[-500:]}")
        # 最后一行 stdout 是 JSON 统计
        stats: dict[str, Any] = {}
        for line in reversed(proc.stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    stats = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        logger.info("nfra crawl done: item_id={} pages={} stats={}", item_id, pages, stats)
        return stats
    except Exception as exc:
        logger.error("nfra crawl task failed: item_id={} pages={} err={}", item_id, pages, exc)
        raise self.retry(exc=exc)


# ── Beat schedule (populated from DB on startup) ────────────

celery_app.conf.beat_schedule: dict[str, Any] = {}


# ── APScheduler integration ─────────────────────────────────

_scheduler: Any = None  # APScheduler instance


async def init_scheduler() -> None:
    """Initialize APScheduler with AsyncIOScheduler."""
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        _scheduler = AsyncIOScheduler()
        _scheduler.start()
        logger.info("APScheduler started")
    except ImportError:
        logger.warning("APScheduler not available, scheduling disabled")


async def close_scheduler() -> None:
    """Shutdown APScheduler."""
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


async def add_scheduled_spider(spider_name: str, schedule: str, spider_config: dict[str, Any]) -> str:
    """Add a spider to the APScheduler with a cron expression.

    Returns the APScheduler job ID.
    """
    if not _scheduler:
        msg = "Scheduler not initialized"
        raise RuntimeError(msg)

    from web_scraper_service.scheduler.triggers import parse_cron

    trigger = parse_cron(schedule)

    def _run_spider() -> None:
        job_id = str(uuid.uuid4())
        crawl_task.delay(spider_name, spider_config, job_id)

    job = _scheduler.add_job(
        _run_spider,
        trigger=trigger,
        id=f"spider:{spider_name}",
        name=f"Schedule {spider_name}",
        replace_existing=True,
    )
    logger.info("Scheduled spider {name} with cron '{cron}', job_id={jid}", name=spider_name, cron=schedule, jid=job.id)
    return job.id


async def remove_scheduled_spider(spider_name: str) -> bool:
    """Remove a spider from the scheduler."""
    if not _scheduler:
        return False
    try:
        _scheduler.remove_job(f"spider:{spider_name}")
        logger.info("Removed scheduled spider {name}", name=spider_name)
        return True
    except Exception:
        return False


async def list_scheduled_jobs() -> list[dict[str, Any]]:
    """List all scheduled APScheduler jobs."""
    if not _scheduler:
        return []
    jobs = _scheduler.get_jobs()
    return [
        {
            "id": j.id,
            "name": j.name,
            "next_run": str(j.next_run_time) if j.next_run_time else None,
            "trigger": str(j.trigger),
        }
        for j in jobs
    ]


async def init_nfra_schedule() -> None:
    """Register the daily nfra crawl (4110 + 4291) with APScheduler."""
    if not settings.nfra_schedule_enabled:
        logger.info("nfra schedule disabled (nfra_schedule_enabled=false)")
        return
    if not _scheduler:
        logger.warning("APScheduler not initialized, nfra schedule skipped")
        return
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger.from_crontab(
        settings.nfra_schedule_cron, timezone="Asia/Shanghai"
    )
    pages = settings.nfra_schedule_pages

    def _run_nfra() -> None:
        for iid in (4110, 4291):
            nfra_crawl_task.delay(iid, pages)
            logger.info("Dispatched nfra crawl: item_id={} pages={}", iid, pages)

    _scheduler.add_job(
        _run_nfra,
        trigger=trigger,
        id="nfra:daily",
        name="nfra daily crawl (4110+4291)",
        replace_existing=True,
    )
    logger.info(
        "Scheduled nfra daily crawl: cron='{}' pages={}",
        settings.nfra_schedule_cron,
        pages,
    )


# ── Entry points for CLI ───────────────────────────────────

def run_worker() -> None:
    """Start Celery worker."""
    celery_app.worker_main(["worker", "--loglevel=info", f"--concurrency={settings.celery_concurrency}"])


def run_beat() -> None:
    """Start Celery beat scheduler."""
    celery_app.worker_main(["beat", "--loglevel=info"])
