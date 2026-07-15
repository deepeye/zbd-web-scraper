"""Diagnostic: why does the nfra detail page time out under browser+proxy?

The capital/equity crawlers fetch detail pages via Scrapling's AsyncDynamicSession,
whose `fetch` calls `page.goto(url)` with Playwright's DEFAULT wait_until="load".
"load" fires only after every subresource (fonts/images/stylesheets/analytics) finishes.
This script isolates whether the 60s goto timeout is caused by:

  (A) the page itself — subresources never finish "load" even with NO proxy
  (B) the proxy — Chromium can't use the qg.net proxy (auth/compat), so even
      "domcontentloaded" never fires through the proxy
  (C) fixable by disable_resources — dropping fonts/images/stylesheets lets "load" fire

Run on the server:
    # Full matrix, direct + with-proxy (pass a proxy from proxy_cache.json)
    python scripts/test_detail_proxy.py --server 123.189.61.60:14398

    # Direct only (no proxy) — isolates (A) vs (B)
    python scripts/test_detail_proxy.py

    # Use a specific failing doc_id
    python scripts/test_detail_proxy.py --doc-id 1264551 --server 123.189.61.60:14398
"""

from __future__ import annotations

import argparse
import asyncio
import time
from urllib.parse import urlparse

from loguru import logger

from web_scraper_service.core.logging import setup_logging
from web_scraper_service.crawlers.nfra import _build_proxy_url, build_detail_html_url

# Resource types whose hang typically prevents "load" from firing on an Angular SPA.
# Mirrors Scrapling's disable_resources set (engines/_browsers/_base.py).
HEAVY_RESOURCE_TYPES = {"image", "font", "media", "stylesheet", "beacon"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Diagnose nfra detail-page goto timeout")
    p.add_argument("--server", type=str, default=None, help="Proxy 'ip:port' to test (qg.net)")
    p.add_argument("--doc-id", type=int, default=1264551, help="Detail doc_id to fetch")
    p.add_argument("--timeout", type=int, default=30, help="Per-test timeout (s)")
    return p.parse_args()


def _proxy_dict(proxy_url: str) -> dict[str, str]:
    p = urlparse(proxy_url)
    return {
        "server": f"{p.scheme}://{p.hostname}:{p.port}",
        "username": p.username or "",
        "password": p.password or "",
    }


async def _httpx_detail(url: str, proxy_url: str | None, timeout: int) -> tuple[bool, str]:
    """Baseline: can we fetch the detail HTML at all (through proxy)?"""
    import httpx

    mounts = None
    if proxy_url:
        mounts = {
            "http://": httpx.AsyncHTTPTransport(proxy=proxy_url),
            "https://": httpx.AsyncHTTPTransport(proxy=proxy_url),
        }
    try:
        async with httpx.AsyncClient(mounts=mounts, timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url)
            ok = r.status_code == 200
            return ok, f"status={r.status_code} bytes={len(r.content)}"
    except Exception as exc:
        return False, f"exc: {exc}"


async def _pw_detail(
    url: str,
    proxy_url: str | None,
    wait_until: str,
    disable_resources: bool,
    timeout_ms: int,
) -> tuple[bool, str, float, str]:
    """Raw Playwright. Returns (ok, status, elapsed_s, snippet_of_rendered_dom)."""
    from playwright.async_api import async_playwright

    proxy_dict = _proxy_dict(proxy_url) if proxy_url else None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(proxy=proxy_dict)
            page = await ctx.new_page()
            if disable_resources:
                async def _abort(route, request):
                    if request.resource_type in HEAVY_RESOURCE_TYPES:
                        await route.abort()
                    else:
                        await route.continue_()
                await page.route("**/*", _abort)
            t0 = time.perf_counter()
            try:
                resp = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                elapsed = time.perf_counter() - t0
                # Probe whether the rendered DOM actually carries extractable content.
                # ItemDetail renders doc number/title into bound elements after bootstrap.
                snippet = await page.evaluate(
                    "() => document.body ? document.body.innerText.slice(0, 120) : '<no body>'"
                )
                return True, f"status={resp.status if resp else 'None'}", elapsed, snippet.replace("\n", " ")
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                return False, f"exc: {exc}", elapsed, ""
        finally:
            await browser.close()


async def _run_case(name: str, coro) -> None:
    logger.info("--- {} ---", name)
    try:
        res = await coro
        logger.info("    -> {}", res)
    except Exception as exc:
        logger.error("    -> unexpected: {}", exc)


async def main() -> int:
    args = parse_args()
    setup_logging()

    url = build_detail_html_url(args.doc_id)
    proxy_url = _build_proxy_url(args.server) if args.server else None
    timeout_ms = args.timeout * 1000

    logger.info("=" * 64)
    logger.info("Detail page goto-timeout diagnostic")
    logger.info("  url        = {}", url)
    logger.info("  proxy      = {}", proxy_url or "NONE (direct)")
    logger.info("  per-test   = {}s", args.timeout)
    logger.info("=" * 64)

    # 1) Baseline: HTML reachable via httpx (with proxy if given)
    await _run_case(
        f"httpx detail HTML (proxy={'yes' if proxy_url else 'no'})",
        _httpx_detail(url, proxy_url, args.timeout),
    )

    # 2) Direct (no proxy): does "load" fire without the proxy?
    #    If this TIMES OUT -> cause (A): the page's subresources hang on their own.
    #    If this SUCCEEDS fast -> the page is fine; the proxy is the differentiator.
    await _run_case(
        "PW direct  wait=load            (no proxy)",
        _pw_detail(url, None, "load", False, timeout_ms),
    )

    # 3) Direct: "domcontentloaded" — the Angular shell arrives early; confirms the
    #    main document + early JS are reachable when we don't wait for all subresources.
    await _run_case(
        "PW direct  wait=domcontentloaded(no proxy)",
        _pw_detail(url, None, "domcontentloaded", False, timeout_ms),
    )

    if proxy_url:
        # 4) Through proxy: "domcontentloaded".
        #    SUCCEEDS -> proxy works for Chromium at the document level (cause A, fixable).
        #    TIMES OUT -> proxy is broken for Chromium (cause B); browser+proxy must go.
        await _run_case(
            "PW proxy   wait=domcontentloaded",
            _pw_detail(url, proxy_url, "domcontentloaded", False, timeout_ms),
        )

        # 5) Through proxy: "load" with heavy resources disabled.
        #    SUCCEEDS -> fix is `disable_resources=True` on the detail fetch.
        #    TIMES OUT -> disabling resources alone is NOT enough.
        await _run_case(
            "PW proxy   wait=load +disable_resources",
            _pw_detail(url, proxy_url, "load", True, timeout_ms),
        )

        # 6) Through proxy: "domcontentloaded" + disable_resources + short networkidle —
        #    closest to what a fixed detail fetch would do.
        await _run_case(
            "PW proxy   wait=domcontentloaded +disable_resources",
            _pw_detail(url, proxy_url, "domcontentloaded", True, timeout_ms),
        )

    logger.info("=" * 64)
    logger.info("Interpretation:")
    logger.info("  - Case 'PW direct wait=load' timing out   => page subresources hang (cause A)")
    logger.info("  - 'PW proxy domcontentloaded' timing out  => proxy broken for Chromium (cause B)")
    logger.info("  - 'PW proxy load +disable_resources' OK   => apply disable_resources=True")
    logger.info("  - Rendered-DOM snippet non-empty          => extraction would succeed w/ proper wait")
    logger.info("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
