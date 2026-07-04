"""FastAPI application entry point with lifespan management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from loguru import logger

from web_scraper_service.api.middleware import add_middlewares
from web_scraper_service.api.v1 import jobs, metrics, nfra, results, spiders
from web_scraper_service.config import settings
from web_scraper_service.core.exceptions import AppError
from web_scraper_service.core.logging import setup_logging
from web_scraper_service.fetchers.proxy import init_proxies
from web_scraper_service.scheduler.engine import (
    close_scheduler,
    init_nfra_schedule,
    init_scheduler,
)
from web_scraper_service.storage.database import close_db, init_db
from web_scraper_service.storage.capital_change_data import init_capital_change_table
from web_scraper_service.storage.djg_data import init_djg_table
from web_scraper_service.storage.equity_change_data import init_equity_change_table
from web_scraper_service.storage.redis import close_redis, init_redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    setup_logging()
    logger.info("Starting {app} in {env} mode", app=settings.app_name, env=settings.app_env)

    await init_db()
    await init_djg_table()
    await init_capital_change_table()
    await init_equity_change_table()
    await init_redis()
    init_proxies()
    await init_scheduler()
    await init_nfra_schedule()

    # Import example spiders to register them
    import web_scraper_service.spiders.examples.static_spider  # noqa: F401
    import web_scraper_service.spiders.examples.spa_spider  # noqa: F401

    logger.info("All services initialized")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await close_scheduler()
    await close_redis()
    await close_db()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Middleware
add_middlewares(app)

# Routes
app.include_router(spiders.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(results.router, prefix="/api/v1")
app.include_router(nfra.router, prefix="/api/v1")
app.include_router(metrics.router, prefix="/api/v1")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    status = 404 if exc.code in (1001,) else 400
    if exc.code >= 3000:
        status = 500
    return JSONResponse(
        status_code=status,
        content={"code": exc.code, "message": exc.message, "data": {"detail": exc.detail}},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


def run_api() -> None:
    import uvicorn

    uvicorn.run(
        "web_scraper_service.main:app",
        host=settings.api_host,
        port=settings.api_port,
        workers=settings.api_workers,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run_api()
