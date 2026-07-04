"""FastAPI lifespan 启动行为：确保 djg_data 表结构在启动时自愈。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from web_scraper_service.main import lifespan


@pytest.mark.asyncio
async def test_lifespan_ensures_djg_table_on_startup() -> None:
    """启动时调用 init_djg_table，保证 djg_data.publish_date 等列自愈。"""
    with patch("web_scraper_service.main.setup_logging", MagicMock()), \
         patch("web_scraper_service.main.init_db", AsyncMock()), \
         patch("web_scraper_service.main.init_redis", AsyncMock()), \
         patch("web_scraper_service.main.init_proxies", MagicMock()), \
         patch("web_scraper_service.main.init_scheduler", AsyncMock()), \
         patch("web_scraper_service.main.init_nfra_schedule", AsyncMock()), \
         patch("web_scraper_service.main.close_scheduler", AsyncMock()), \
         patch("web_scraper_service.main.close_redis", AsyncMock()), \
         patch("web_scraper_service.main.close_db", AsyncMock()), \
         patch(
             "web_scraper_service.main.init_djg_table",
             new=AsyncMock(),
             create=True,
         ) as init_djg, \
         patch(
             "web_scraper_service.main.init_capital_change_table",
             new=AsyncMock(),
             create=True,
         ), \
         patch(
             "web_scraper_service.main.init_equity_change_table",
             new=AsyncMock(),
             create=True,
         ):
        app = FastAPI()
        async with lifespan(app):
            pass
    init_djg.assert_awaited_once()
