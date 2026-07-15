"""Standalone proxy connectivity test for nfra.gov.cn.

Usage (inside the project container):
    python scripts/test_proxy.py

This script tests the proxy configuration directly with Playwright,
bypassing Scrapling and the full crawler logic.
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger

from web_scraper_service.core.logging import setup_logging
from web_scraper_service.crawlers.nfra import _build_proxy_url


TARGET = "https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=923&itemId=4291&itemUrl=ItemListRightList.html&itemName=zhujiguan"


async def test_with_scrapling() -> bool:
    """Test proxy via Scrapling's AsyncStealthySession (same as discover_doc_rows)."""
    from scrapling.fetchers import AsyncStealthySession

    proxy_url = _build_proxy_url()
    logger.info("Scrapling test: proxy_url={}", proxy_url or "None")

    kwargs: dict[str, str] = {"headless": "True"}
    if proxy_url:
        kwargs["proxy"] = proxy_url

    session = AsyncStealthySession(**kwargs)
    try:
        await session.__aenter__()
        resp = await session.fetch(TARGET, network_idle=True, timeout=20000)
        logger.info("Scrapling test: SUCCESS, status={}", resp.status)
        return True
    except Exception as exc:
        logger.error("Scrapling test: FAILED - {}", exc)
        return False
    finally:
        try:
            await session.__aexit__(None, None, None)
        except Exception:
            pass


async def test_with_raw_playwright() -> bool:
    """Test proxy via raw Playwright (bypass Scrapling)."""
    from playwright.async_api import async_playwright

    proxy_url = _build_proxy_url()
    logger.info("Raw Playwright test: proxy_url={}", proxy_url or "None")

    proxy_dict: dict[str, str] | None = None
    if proxy_url:
        from urllib.parse import urlparse

        p = urlparse(proxy_url)
        proxy_dict = {
            "server": f"{p.scheme}://{p.hostname}:{p.port}",
            "username": p.username or "",
            "password": p.password or "",
        }
        logger.info("Raw Playwright test: proxy_dict={}", proxy_dict)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(proxy=proxy_dict)
            page = await context.new_page()
            resp = await page.goto(TARGET, wait_until="load", timeout=20000)
            logger.info("Raw Playwright test: SUCCESS, status={}", resp.status if resp else "None")
            return True
        except Exception as exc:
            logger.error("Raw Playwright test: FAILED - {}", exc)
            return False
        finally:
            await browser.close()


async def test_with_httpx() -> bool:
    """Test proxy via httpx (same protocol as curl)."""
    import httpx

    proxy_url = _build_proxy_url()
    logger.info("httpx test: proxy_url={}", proxy_url or "None")

    proxies: dict[str, str] | None = None
    if proxy_url:
        proxies = {"http://": proxy_url, "https://": proxy_url}

    try:
        async with httpx.AsyncClient(proxies=proxies, timeout=20) as client:
            resp = await client.get(TARGET)
            logger.info("httpx test: SUCCESS, status={}", resp.status_code)
            return True
    except Exception as exc:
        logger.error("httpx test: FAILED - {}", exc)
        return False


async def main() -> int:
    setup_logging()
    logger.info("=" * 60)
    logger.info("Proxy connectivity test for {}", TARGET)
    logger.info("=" * 60)

    results: dict[str, bool] = {}

    logger.info("\n--- Test 1: httpx (baseline) ---")
    results["httpx"] = await test_with_httpx()

    logger.info("\n--- Test 2: Raw Playwright ---")
    results["raw_playwright"] = await test_with_raw_playwright()

    logger.info("\n--- Test 3: Scrapling AsyncStealthySession ---")
    results["scrapling"] = await test_with_scrapling()

    logger.info("\n" + "=" * 60)
    logger.info("Results:")
    for name, ok in results.items():
        logger.info("  {}: {}", name, "PASS" if ok else "FAIL")
    logger.info("=" * 60)

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
