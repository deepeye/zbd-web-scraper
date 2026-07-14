# Design: nfra DJG Route Rename

**Date:** 2026-07-13
**Status:** approved

## Goal

Rename three nfra DJG (任职资格) API routes to add `/djg` prefix, making the URL structure consistent with the existing `/nfra/capital/` and `/nfra/equity/` sub-modules.

## Current vs Target

| Old Route | New Route |
|-----------|-----------|
| `POST /api/v1/nfra/crawl` | `POST /api/v1/nfra/djg/crawl` |
| `GET /api/v1/nfra/crawl/{job_id}` | `GET /api/v1/nfra/djg/crawl/{job_id}` |
| `GET /api/v1/nfra/data` | `GET /api/v1/nfra/djg/data` |

Capital and equity routes (`/nfra/capital/*`, `/nfra/equity/*`) are unchanged.

## Breaking Change

Old routes are replaced directly — no redirect, no backward compatibility. Callers must update to the new URLs.

## Files to Change

### Source (1 file)

**`src/web_scraper_service/api/v1/nfra.py`** — 3 route decorators:

- Line 46: `@router.post("/crawl")` → `@router.post("/djg/crawl")`
- Line 66: `@router.get("/crawl/{job_id}")` → `@router.get("/djg/crawl/{job_id}")`
- Line 250: `@router.get("/data")` → `@router.get("/djg/data")`

No logic, parameters, or response format changes.

### Tests (1 file)

**`tests/test_api/test_nfra.py`** — all URL string references to the 3 renamed routes (~10 URL strings across ~22 occurrences).

### Docs (5 files)

- `CLAUDE.md` — 3 references
- `README.md` — 3 references
- `README.zh-CN.md` — 3 references
- `docs/API.md` — 5 references
- `docs/DEPLOY.md` — 4 references

### Not Changed

- Historical plan/spec docs under `docs/superpowers/` — these are records of past work
- Capital/equity routes, Celery tasks, scheduler, extractors — unaffected