# nfra DJG Route Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename three nfra DJG API routes to add `/djg` prefix for consistency with capital/equity sub-modules.

**Architecture:** Direct route decorator changes in `api/v1/nfra.py`, corresponding URL string updates in tests and docs. No logic changes, no backward compatibility.

**Tech Stack:** FastAPI, pytest

## Global Constraints

- Direct replacement — no redirect, no backward compatibility
- Route logic, parameters, and response format unchanged
- Capital/equity routes (`/nfra/capital/*`, `/nfra/equity/*`) unchanged
- Historical plan/spec docs under `docs/superpowers/` not modified

---

### Task 1: Rename routes in source

**Files:**
- Modify: `src/web_scraper_service/api/v1/nfra.py:46,66,250`

**Interfaces:**
- Consumes: nothing
- Produces: `POST /api/v1/nfra/djg/crawl`, `GET /api/v1/nfra/djg/crawl/{job_id}`, `GET /api/v1/nfra/djg/data`

- [ ] **Step 1: Change three route decorators**

In `src/web_scraper_service/api/v1/nfra.py`:

Line 46 — change:
```python
@router.post("/crawl")
```
to:
```python
@router.post("/djg/crawl")
```

Line 66 — change:
```python
@router.get("/crawl/{job_id}")
```
to:
```python
@router.get("/djg/crawl/{job_id}")
```

Line 250 — change:
```python
@router.get("/data")
```
to:
```python
@router.get("/djg/data")
```

- [ ] **Step 2: Verify old routes return 404, new routes work**

```bash
# Old routes should 404
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/api/v1/nfra/crawl -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d '{}'
# Expected: 404

curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/nfra/data -H "X-API-Key: $API_KEY"
# Expected: 404

# New routes should work
curl -s -X POST http://localhost:8000/api/v1/nfra/djg/crawl -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d '{}' | python3 -m json.tool
# Expected: 200 with job_id, item_id, pages, status

curl -s http://localhost:8000/api/v1/nfra/djg/crawl/fake-id -H "X-API-Key: $API_KEY" | python3 -m json.tool
# Expected: 200 with job_id, status, result

curl -s "http://localhost:8000/api/v1/nfra/djg/data?page=1&size=5" -H "X-API-Key: $API_KEY" | python3 -m json.tool
# Expected: 200 with data array and pagination
```

- [ ] **Step 3: Commit**

```bash
git add src/web_scraper_service/api/v1/nfra.py
git commit -m "refactor(nfra): rename djg routes to /nfra/djg/* for consistency"
```

---

### Task 2: Update test URLs

**Files:**
- Modify: `tests/test_api/test_nfra.py:41,62,76,85,96,113,285,319,332,347,360`

**Interfaces:**
- Consumes: `POST /api/v1/nfra/djg/crawl`, `GET /api/v1/nfra/djg/crawl/{job_id}`, `GET /api/v1/nfra/djg/data` (from Task 1)

- [ ] **Step 1: Replace all old URL strings in tests**

In `tests/test_api/test_nfra.py`, replace all occurrences:

| Line | Old | New |
|------|-----|-----|
| 41 | `"/api/v1/nfra/crawl"` | `"/api/v1/nfra/djg/crawl"` |
| 62 | `"/api/v1/nfra/crawl"` | `"/api/v1/nfra/djg/crawl"` |
| 76 | `"/api/v1/nfra/crawl"` | `"/api/v1/nfra/djg/crawl"` |
| 85 | `"/api/v1/nfra/crawl"` | `"/api/v1/nfra/djg/crawl"` |
| 96 | `"/api/v1/nfra/crawl/job-1"` | `"/api/v1/nfra/djg/crawl/job-1"` |
| 113 | `"/api/v1/nfra/crawl/job-2"` | `"/api/v1/nfra/djg/crawl/job-2"` |
| 285 | `"/api/v1/nfra/data"` | `"/api/v1/nfra/djg/data"` |
| 319 | `"/api/v1/nfra/data"` | `"/api/v1/nfra/djg/data"` |
| 332 | `"/api/v1/nfra/data"` | `"/api/v1/nfra/djg/data"` |
| 347 | `"/api/v1/nfra/data"` | `"/api/v1/nfra/djg/data"` |
| 360 | `"/api/v1/nfra/data"` | `"/api/v1/nfra/djg/data"` |

- [ ] **Step 2: Run tests to verify all pass**

```bash
uv run pytest tests/test_api/test_nfra.py -v
```
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_api/test_nfra.py
git commit -m "test(nfra): update test URLs to /nfra/djg/* routes"
```

---

### Task 3: Update documentation

**Files:**
- Modify: `CLAUDE.md:160-162`
- Modify: `README.md:103,231-233`
- Modify: `README.zh-CN.md:103,231-233`
- Modify: `docs/API.md:338,368,375,414,450`
- Modify: `docs/DEPLOY.md:48,52,63`

- [ ] **Step 1: Update CLAUDE.md**

Lines 160-162 — change:
```
| POST | `/api/v1/nfra/crawl` | 任职资格：手动触发 ...
| GET | `/api/v1/nfra/crawl/{job_id}` | 轮询 Celery 任务状态 |
| GET | `/api/v1/nfra/data` | 按 `crawl_time` 范围分页查询 `djg_data` |
```
to:
```
| POST | `/api/v1/nfra/djg/crawl` | 任职资格：手动触发 ...
| GET | `/api/v1/nfra/djg/crawl/{job_id}` | 轮询 Celery 任务状态 |
| GET | `/api/v1/nfra/djg/data` | 按 `crawl_time` 范围分页查询 `djg_data` |
```

Also update line 178 reference to `POST /api/v1/nfra/crawl` → `POST /api/v1/nfra/djg/crawl`.

- [ ] **Step 2: Update README.md**

Lines 103, 231-233 — replace `/api/v1/nfra/crawl` → `/api/v1/nfra/djg/crawl`, `/api/v1/nfra/data` → `/api/v1/nfra/djg/data`.

- [ ] **Step 3: Update README.zh-CN.md**

Lines 103, 231-233 — same replacements as README.md.

- [ ] **Step 4: Update docs/API.md**

Section 7.1 (line 338): title `POST /api/v1/nfra/crawl` → `POST /api/v1/nfra/djg/crawl`
Line 368: curl example URL update
Section 7.2 (line 375): title `GET /api/v1/nfra/crawl/{job_id}` → `GET /api/v1/nfra/djg/crawl/{job_id}`
Section 7.3 (line 414): title `GET /api/v1/nfra/data` → `GET /api/v1/nfra/djg/data`
Line 450: curl example URL update

- [ ] **Step 5: Update docs/DEPLOY.md**

Lines 48, 52, 63 — replace `/api/v1/nfra/crawl` → `/api/v1/nfra/djg/crawl`, `/api/v1/nfra/data` → `/api/v1/nfra/djg/data`.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md README.zh-CN.md docs/API.md docs/DEPLOY.md
git commit -m "docs: update nfra djg route references to /nfra/djg/*"
```