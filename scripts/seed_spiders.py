"""Seed the database with example spider configurations."""

from __future__ import annotations

import asyncio
import uuid

from web_scraper_service.storage.database import async_session_factory, init_db
from web_scraper_service.storage.models import SpiderModel


SEED_DATA = [
    {
        "name": "quotes_static",
        "url": "https://quotes.toscrape.com/",
        "schedule": "0 */6 * * *",
        "use_playwright": False,
        "use_stealthy": True,
        "concurrency": 5,
        "proxy_enabled": False,
        "retry_times": 3,
        "download_delay": 0.5,
    },
    {
        "name": "quotes_spa",
        "url": "https://quotes.toscrape.com/js/",
        "schedule": "0 */12 * * *",
        "use_playwright": True,
        "use_stealthy": False,
        "concurrency": 3,
        "proxy_enabled": False,
        "retry_times": 3,
        "download_delay": 1.0,
    },
]


async def main() -> None:
    await init_db()
    async with async_session_factory() as session:
        for data in SEED_DATA:
            existing = await session.execute(
                __import__("sqlalchemy").select(SpiderModel).where(SpiderModel.name == data["name"])
            )
            if existing.scalar_one_or_none():
                print(f"Spider '{data['name']}' already exists, skipping")
                continue
            spider = SpiderModel(**data)
            session.add(spider)
            print(f"Created spider: {data['name']}")
        await session.commit()
    print("Seed complete")


if __name__ == "__main__":
    asyncio.run(main())
