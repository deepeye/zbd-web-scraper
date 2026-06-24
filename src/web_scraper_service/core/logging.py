from __future__ import annotations

import sys
from contextvars import ContextVar

from loguru import logger

from web_scraper_service.config import settings

# Context variables for structured logging
job_id_var: ContextVar[str] = ContextVar("job_id", default="")
spider_name_var: ContextVar[str] = ContextVar("spider_name", default="")


def _log_format(record: object) -> str:
    jid = job_id_var.get("")
    sname = spider_name_var.get("")
    extra = ""
    if jid:
        extra += f" job_id={jid}"
    if sname:
        extra += f" spider={sname}"
    if settings.log_json:
        return "{{\"time\":\"{time:YYYY-MM-DD HH:mm:ss.SSS}\",\"level\":\"{level}\",\"message\":\"{message}\"{extra}}}\n"
    return "<green>{time:YYYY-MM-DD HH:mm:ss}</> | <level>{level: <8}</> | <cyan>{name}</>:<cyan>{function}</>:<cyan>{line}</>{extra} | <level>{message}</>\n"


def setup_logging() -> None:
    logger.remove()
    fmt = _log_format if not settings.log_json else _log_format
    logger.add(
        sys.stderr,
        format=fmt,
        level=settings.log_level,
        colorize=not settings.log_json,
    )
    logger.add(
        "logs/scraper_{time:YYYY-MM-DD}.log",
        format=fmt,
        level=settings.log_level,
        rotation="00:00",
        retention="30 days",
        compression="gz",
    )
