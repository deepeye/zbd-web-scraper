# Proxy Pool URL Parameters & 403 Handling Design

## Date

2026-07-15

## Background

The dynamic proxy pool (`DynamicProxyPool`) fetches proxy IPs from a remote API (qg.net) and rotates through them. Two operational requirements were raised:

1. The proxy fetch request should use `num=1` and a default validity period of `keep_alive=1440` minutes.
2. When the crawler receives a `403 Forbidden` from the target site, it should re-fetch a fresh proxy IP.

## Decisions

1. **`PROXY_POOL_URL` remains a complete URL.** The caller configures the full URL including qg.net query parameters. The application does **not** append or override `num` / `keep_alive` automatically.
2. **No code changes are required for 403 handling.** The existing `nfra.py` crawler already marks the current proxy as failed, switches to the next cached proxy, and refreshes the pool from the API after a 5-minute cooldown when the pool is exhausted.
3. This design is documentation-only: update `.env.example` and `CLAUDE.md` so operators know the expected URL format and how 403s are handled.

## Configuration

Set `PROXY_POOL_URL` in `.env` to include the required query parameters:

```env
PROXY_POOL_URL=https://exclusive.proxy.qg.net/get?key=YOUR_KEY&num=1&keep_alive=1440
```

- `num=1`: fetch one proxy IP per request.
- `keep_alive=1440`: proxy validity period in minutes (24 hours).

If these parameters are omitted, the proxy provider may return `400 Bad Request`. The existing `DynamicProxyPool._fetch_all()` already retries on `400 Bad Request` every 5 minutes until it succeeds.

## 403 Handling

In `src/web_scraper_service/crawlers/nfra.py::discover_doc_rows()`:

1. On any page-level error (including `403 Forbidden`), the current browser session is closed.
2. `_rebuild_session_with_proxy()` is called:
   - The current proxy is marked as failed.
   - The next available proxy from `DynamicProxyPool` is used.
   - If the pool is exhausted, `wait_and_refresh()` waits 5 minutes then re-fetches from `PROXY_POOL_URL`.
3. The failed page is retried once with the new proxy.

This satisfies “re-fetch proxy IP on 403” without introducing immediate API refreshes or extra code paths.

## Files to Change

- `.env.example`: update `PROXY_POOL_URL` comment with the `num=1&keep_alive=1440` example.
- `CLAUDE.md`: add a short note in the proxy pool section about the expected URL format and the built-in 403/proxy-rotation behavior.

## Out of Scope

- No changes to `src/web_scraper_service/fetchers/dynamic_proxy.py`.
- No new settings for `num` or `keep_alive`.
- No immediate/forced API refresh on 403; the existing exhausted-pool refresh path is preserved.
