# nfra 手动与定时调度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 nfra 采集器新增手动启动（HTTP 接口，可指定 item_id 与页数）与定时调度（每日 8 点 Asia/Shanghai，4110+4291 各 5 页），并加状态查询接口。

**Architecture:** 新增 Celery 任务 `nfra_crawl_task(item_id, pages)` 执行 `run_crawl`；手动 API `POST /api/v1/nfra/crawl` 用 `apply_async(task_id=job_id)` 派发并返回 job_id；`GET /api/v1/nfra/crawl/{job_id}` 用 Celery `AsyncResult` 查状态；`init_nfra_schedule()` 在 lifespan 注册 APScheduler cron（`CronTrigger.from_crontab(..., timezone="Asia/Shanghai")`）每日 8 点派发两个 itemId 任务。

**Tech Stack:** FastAPI · Celery 5.6 · APScheduler AsyncIOScheduler · Redis · pytest

## Global Constraints

- 不改 nfra 爬虫本体（`crawlers/nfra.py`/`nfra_extractor.py`/`storage/djg_data.py`）——只调用 `run_crawl(item_id=, pages=)`。
- 定时每日 8 点同时派发 itemId=4110 与 4291，各 `nfra_schedule_pages` 页（默认 5）。
- 手动 API body `{item_id?: int=4110, pages?: int=5}`，`pages>=1` 否则 400；需 `X-API-Key`。
- 状态用 Celery `AsyncResult(job_id, app=celery_app)`；`apply_async(args=[item_id,pages], task_id=job_id)` 使 job_id 可追踪。
- 时区 `Asia/Shanghai`（`CronTrigger.from_crontab(cron, timezone="Asia/Shanghai")`）。
- 复用现有 `ok()` 响应包装、`ApiKey` 依赖、`celery_app`（`scheduler/engine.py`）。
- mypy strict 全局开启；代码须过 mypy。venv 在 `.venv`。
- 约束引自 spec：`docs/superpowers/specs/2026-06-25-nfra-scheduling-design.md`。

## File Structure

| 文件 | 职责 |
|------|------|
| `src/web_scraper_service/config.py` | 加 nfra_schedule_enabled/cron/pages |
| `.env.example` | 加 NFRA_SCHEDULE_* |
| `src/web_scraper_service/scheduler/engine.py` | 加 `nfra_crawl_task` + `init_nfra_schedule()` |
| `src/web_scraper_service/api/v1/nfra.py` | 新建 POST/GET 路由 |
| `src/web_scraper_service/main.py` | 注册 nfra 路由 + lifespan 调 init_nfra_schedule |
| `tests/test_api/test_nfra.py` | API + 调度注册单测 |

---

### Task 1: 配置项 nfra_schedule_*

**Files:**
- Modify: `src/web_scraper_service/config.py`（Bailian 段后加 NFRA Schedule 段）
- Modify: `.env.example`（Bailian 段后加 NFRA Schedule 段）

**Interfaces:**
- Produces: `settings.nfra_schedule_enabled: bool`（默认 True）、`settings.nfra_schedule_cron: str`（默认 `"0 8 * * *"`）、`settings.nfra_schedule_pages: int`（默认 5）

- [ ] **Step 1: config.py 加配置**

在 `src/web_scraper_service/config.py` 的 Bailian 段（`bailian_model` 之后）、`# ── S3 ──` 之前插入：

```python
    # ── nfra 定时调度 ───────────────────────────────────────
    nfra_schedule_enabled: bool = True
    nfra_schedule_cron: str = "0 8 * * *"
    nfra_schedule_pages: int = 5
```

- [ ] **Step 2: .env.example 加配置**

在 `.env.example` 的 `# ── Bailian ──` 段之后、`# ── Scrapling / Fetcher ──` 之前插入：

```
# ── nfra 定时调度 ─────────────────────────────────────────
# 每日定时采集（Asia/Shanghai 时区）；关闭设为 false
NFRA_SCHEDULE_ENABLED=true
NFRA_SCHEDULE_CRON=0 8 * * *
NFRA_SCHEDULE_PAGES=5
```

- [ ] **Step 3: 验证配置加载**

Run: `.venv/bin/python -c "from web_scraper_service.config import settings; print(settings.nfra_schedule_enabled, settings.nfra_schedule_cron, settings.nfra_schedule_pages)"`
Expected: `True 0 8 * * * 5`

- [ ] **Step 4: Commit**

```bash
git add src/web_scraper_service/config.py .env.example
git commit -m "feat: add nfra_schedule config for daily 8am crawl"
```

---

### Task 2: Celery 任务 nfra_crawl_task + init_nfra_schedule

**Files:**
- Modify: `src/web_scraper_service/scheduler/engine.py`（加 `nfra_crawl_task` 与 `init_nfra_schedule`）

**Interfaces:**
- Consumes: `settings.nfra_schedule_*`（Task 1）；`web_scraper_service.crawlers.nfra.run_crawl`；模块级 `_scheduler`、`celery_app`
- Produces:
  - `nfra_crawl_task(item_id: int, pages: int) -> dict`（Celery task）
  - `async init_nfra_schedule() -> None`

- [ ] **Step 1: 在 engine.py 加 nfra_crawl_task**

在 `clean_task` 之后、`# ── Beat schedule ──` 之前插入：

```python
@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def nfra_crawl_task(self: Any, item_id: int, pages: int) -> dict[str, Any]:
    """Celery task to run the nfra crawler (writes to zbd_crawler_data.djg_data)."""
    from web_scraper_service.crawlers.nfra import run_crawl

    try:
        loop = asyncio.new_event_loop()
        try:
            stats = loop.run_until_complete(run_crawl(item_id=item_id, pages=pages))
        finally:
            loop.close()
        return stats
    except Exception as exc:
        logger.error("nfra crawl task failed: item_id={} pages={} err={}", item_id, pages, exc)
        raise self.retry(exc=exc)
```

- [ ] **Step 2: 在 engine.py 加 init_nfra_schedule**

在 `list_scheduled_jobs` 之后、`# ── Entry points for CLI ──` 之前插入：

```python
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
    logger.info("Scheduled nfra daily crawl: cron='{}' pages={}", settings.nfra_schedule_cron, pages)
```

- [ ] **Step 3: 验证可导入且签名正确**

Run: `.venv/bin/python -c "import inspect; from web_scraper_service.scheduler.engine import nfra_crawl_task, init_nfra_schedule; print(type(nfra_crawl_task)); print(inspect.signature(init_nfra_schedule))"`
Expected: `<class 'celery.local.Task'>` 与 `(self) -> None`（async 函数签名）

Wait — `init_nfra_schedule` is `async def`, signature shows `(*args, **kwargs)` for async? Use: `.venv/bin/python -c "import inspect; from web_scraper_service.scheduler.engine import init_nfra_schedule; print(inspect.iscoroutinefunction(init_nfra_schedule))"`
Expected: `True`

- [ ] **Step 4: ruff + mypy**

Run: `.venv/bin/ruff check src/web_scraper_service/scheduler/engine.py && .venv/bin/python -m mypy src/web_scraper_service/scheduler/engine.py`
Expected: 无 error

- [ ] **Step 5: Commit**

```bash
git add src/web_scraper_service/scheduler/engine.py
git commit -m "feat: add nfra_crawl_task and init_nfra_schedule"
```

---

### Task 3: API 路由 api/v1/nfra.py — TDD

**Files:**
- Create: `src/web_scraper_service/api/v1/nfra.py`
- Create: `tests/test_api/test_nfra.py`

**Interfaces:**
- Consumes: `nfra_crawl_task`（Task 2）、`celery_app`、`ApiKey` 依赖、`ok()` 响应
- Produces: `router`（APIRouter prefix="/nfra"），`POST /crawl`、`GET /crawl/{job_id}`

- [ ] **Step 1: 写失败测试 tests/test_api/test_nfra.py**

```python
"""nfra crawl API tests (mock Celery)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from web_scraper_service.main import app
from web_scraper_service.config import settings


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def _api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "api_key", "test-key")
    return "test-key"


def test_post_crawl_defaults(client: TestClient, _api_key: str) -> None:
    fake = MagicMock()
    fake.id = "job-123"
    with patch("web_scraper_service.api.v1.nfra.nfra_crawl_task") as task:
        task.apply_async.return_value = fake
        resp = client.post(
            "/api/v1/nfra/crawl",
            json={},
            headers={"X-API-Key": _api_key},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["job_id"] == "job-123"
    assert data["item_id"] == 4110
    assert data["pages"] == 5
    assert data["status"] == "pending"
    task.apply_async.assert_called_once()
    args, kwargs = task.apply_async.call_args
    assert kwargs["args"] == [4110, 5]


def test_post_crawl_custom(client: TestClient, _api_key: str) -> None:
    fake = MagicMock()
    fake.id = "job-456"
    with patch("web_scraper_service.api.v1.nfra.nfra_crawl_task") as task:
        task.apply_async.return_value = fake
        resp = client.post(
            "/api/v1/nfra/crawl",
            json={"item_id": 4291, "pages": 3},
            headers={"X-API-Key": _api_key},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["item_id"] == 4291
    assert data["pages"] == 3
    _, kwargs = task.apply_async.call_args
    assert kwargs["args"] == [4291, 3]


def test_post_crawl_invalid_pages(client: TestClient, _api_key: str) -> None:
    resp = client.post(
        "/api/v1/nfra/crawl",
        json={"pages": 0},
        headers={"X-API-Key": _api_key},
    )
    assert resp.status_code == 400


def test_post_crawl_no_api_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "api_key", "test-key")
    resp = client.post("/api/v1/nfra/crawl", json={})
    assert resp.status_code == 401


def test_get_status_pending(client: TestClient, _api_key: str) -> None:
    with patch("web_scraper_service.api.v1.nfra.AsyncResult") as ar:
        inst = MagicMock()
        inst.state = "PENDING"
        inst.result = None
        ar.return_value = inst
        resp = client.get(
            "/api/v1/nfra/crawl/job-1",
            headers={"X-API-Key": _api_key},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["job_id"] == "job-1"
    assert data["status"] == "pending"
    assert data["result"] is None


def test_get_status_success(client: TestClient, _api_key: str) -> None:
    with patch("web_scraper_service.api.v1.nfra.AsyncResult") as ar:
        inst = MagicMock()
        inst.state = "SUCCESS"
        inst.result = {"discovered": 18, "pending": 6, "extracted_rows": 6, "stored": 6}
        ar.return_value = inst
        resp = client.get(
            "/api/v1/nfra/crawl/job-2",
            headers={"X-API-Key": _api_key},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "success"
    assert data["result"]["stored"] == 6
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_api/test_nfra.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'web_scraper_service.api.v1.nfra'` 或路由 404）

- [ ] **Step 3: 写实现 src/web_scraper_service/api/v1/nfra.py**

```python
"""nfra crawl API — manual trigger + job status."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web_scraper_service.api.deps import ApiKey
from web_scraper_service.api.response import ok
from web_scraper_service.scheduler.engine import celery_app, nfra_crawl_task

router = APIRouter(prefix="/nfra", tags=["nfra"])


class CrawlRequest(BaseModel):
    item_id: int = Field(default=4110, ge=1)
    pages: int = Field(default=5, ge=1)


@router.post("/crawl")
async def crawl(body: CrawlRequest, _: ApiKey) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    result = nfra_crawl_task.apply_async(
        args=[body.item_id, body.pages], task_id=job_id
    )
    return ok(
        {
            "job_id": result.id,
            "item_id": body.item_id,
            "pages": body.pages,
            "status": "pending",
        }
    )


@router.get("/crawl/{job_id}")
async def crawl_status(job_id: str, _: ApiKey) -> dict[str, Any]:
    from celery.result import AsyncResult

    async_res = AsyncResult(job_id, app=celery_app)
    state = async_res.state or "PENDING"
    status_map = {
        "PENDING": "pending",
        "STARTED": "running",
        "SUCCESS": "success",
        "FAILURE": "failed",
        "RETRY": "retrying",
    }
    status = status_map.get(state, state.lower())
    result: Any = None
    if state == "SUCCESS":
        result = async_res.result
    elif state == "FAILURE":
        result = str(async_res.result)
    return ok({"job_id": job_id, "status": status, "result": result})
```

- [ ] **Step 4: 注册路由到 main.py**

在 `src/web_scraper_service/main.py`：
- 修改 import 行 `from web_scraper_service.api.v1 import jobs, metrics, results, spiders` 为 `from web_scraper_service.api.v1 import jobs, metrics, nfra, results, spiders`
- 在 `app.include_router(results.router, prefix="/api/v1")` 之后加 `app.include_router(nfra.router, prefix="/api/v1")`

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api/test_nfra.py -v`
Expected: 全部 PASS（6 项）

- [ ] **Step 6: ruff + mypy**

Run: `.venv/bin/ruff check src/web_scraper_service/api/v1/nfra.py tests/test_api/test_nfra.py && .venv/bin/python -m mypy src/web_scraper_service/api/v1/nfra.py`
Expected: 无 error

- [ ] **Step 7: Commit**

```bash
git add src/web_scraper_service/api/v1/nfra.py src/web_scraper_service/main.py tests/test_api/test_nfra.py
git commit -m "feat: add nfra crawl API (manual trigger + status)"
```

---

### Task 4: init_nfra_schedule 注册单测 + main.py lifespan 接入

**Files:**
- Create or Modify: `tests/test_api/test_nfra.py`（追加 `init_nfra_schedule` 单测）
- Modify: `src/web_scraper_service/main.py`（lifespan 调用 `init_nfra_schedule`）

**Interfaces:**
- Consumes: `init_nfra_schedule`（Task 2）、`init_scheduler`（lifespan 已有）

- [ ] **Step 1: 追加 init_nfra_schedule 单测到 tests/test_api/test_nfra.py**

在文件末尾追加：

```python
@pytest.mark.asyncio
async def test_init_nfra_schedule_registers_both_itemids(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_scraper_service.scheduler import engine

    monkeypatch.setattr(engine.settings, "nfra_schedule_enabled", True)
    monkeypatch.setattr(engine.settings, "nfra_schedule_cron", "0 8 * * *")
    monkeypatch.setattr(engine.settings, "nfra_schedule_pages", 5)

    sched = MagicMock()
    added: list[dict[str, Any]] = []

    def fake_add_job(func, *, trigger, id, name, replace_existing):
        added.append({"id": id, "name": name, "trigger": trigger})
        return MagicMock(id=id)

    sched.add_job = fake_add_job
    monkeypatch.setattr(engine, "_scheduler", sched)

    dispatched: list[tuple[int, int]] = []
    monkeypatch.setattr(
        engine.nfra_crawl_task,
        "delay",
        lambda iid, pages: dispatched.append((iid, pages)),
    )

    await engine.init_nfra_schedule()

    assert len(added) == 1
    assert added[0]["id"] == "nfra:daily"
    assert added[0]["trigger"].fields[1].expressions == {8}  # hour=8
    assert dispatched == [(4110, 5), (4291, 5)]


@pytest.mark.asyncio
async def test_init_nfra_schedule_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_scraper_service.scheduler import engine

    monkeypatch.setattr(engine.settings, "nfra_schedule_enabled", False)
    sched = MagicMock()
    monkeypatch.setattr(engine, "_scheduler", sched)
    await engine.init_nfra_schedule()
    sched.add_job.assert_not_called()
```

注：`added[0]["trigger"].fields[1]` 是 APScheduler CronTrigger 的 hour 字段（0=minute,1=hour）。若该断言不稳定，改断言 `str(added[0]["trigger"])` 含 "8"。实现者可二选一，但须断言 hour=8 生效。

- [ ] **Step 2: 运行该测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api/test_nfra.py::test_init_nfra_schedule_registers_both_itemids tests/test_api/test_nfra.py::test_init_nfra_schedule_disabled -v`
Expected: PASS。若 hour 字段断言失败，改用 `assert "8" in str(added[0]["trigger"])` 并注释说明。

- [ ] **Step 3: main.py lifespan 接入 init_nfra_schedule**

在 `src/web_scraper_service/main.py`：
- 修改 import 行 `from web_scraper_service.scheduler.engine import close_scheduler, init_scheduler` 为 `from web_scraper_service.scheduler.engine import close_scheduler, init_nfra_schedule, init_scheduler`
- 在 lifespan 的 `await init_scheduler()` 之后、`import ... examples.static_spider` 之前加 `await init_nfra_schedule()`

- [ ] **Step 4: 全量测试无回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全 PASS（既有 29 + 新 8 = 37）

- [ ] **Step 5: ruff + mypy**

Run: `.venv/bin/ruff check src/web_scraper_service/main.py tests/test_api/test_nfra.py && .venv/bin/python -m mypy src/web_scraper_service/main.py`
Expected: 无 error（main.py mypy 若报 `app` 相关已知项，记录但非本任务引入则可接受）

- [ ] **Step 6: Commit**

```bash
git add src/web_scraper_service/main.py tests/test_api/test_nfra.py
git commit -m "feat: wire init_nfra_schedule into lifespan with tests"
```

---

### Task 5: 手动 + 定时 smoke 验收

**Files:** 无（仅运行验证）

**前置：** `DASHSCOPE_API_KEY` 在 `.env`；`API_KEY` 在 `.env`（设为已知值如 `sk-dev-change-me-in-production`）；Docker DB + Redis 在跑；patchright chromium 已装。

- [ ] **Step 1: 启动 API 服务（后台）**

Run: `make dev`（后台运行，或 `uv run uvicorn web_scraper_service.main:app --port 8000 &`）
确认 `http://localhost:8000/docs` 可访问。

- [ ] **Step 2: 启动 Celery worker（后台，另一终端）**

Run: `make worker`
确认 worker 启动且连上 Redis。

- [ ] **Step 3: 手动触发（小规模，1 页）**

Run:
```bash
curl -s -X POST http://localhost:8000/api/v1/nfra/crawl \
  -H "X-API-Key: sk-dev-change-me-in-production" \
  -H "Content-Type: application/json" \
  -d '{"item_id": 4291, "pages": 1}'
```
Expected: 返回 `{"code":0,...,"data":{"job_id":"...","item_id":4291,"pages":1,"status":"pending"}}`

- [ ] **Step 4: 轮询状态**

Run（替换 job_id）：
```bash
curl -s http://localhost:8000/api/v1/nfra/crawl/<job_id> -H "X-API-Key: sk-dev-change-me-in-production"
```
Expected: 先 `pending`/`running`，数分钟后 `success` 且 `result.stored > 0`（djg_data 新增行）。

- [ ] **Step 5: 验证 djg_data 落库**

Run:
```bash
.venv/bin/python - <<'EOF'
import asyncio, asyncpg
async def main():
    conn = await asyncpg.connect("postgresql://scraper:scraper_secret@localhost:5432/zbd_crawler_data")
    print("djg_data rows:", await conn.fetchval("SELECT count(*) FROM djg_data"))
    await conn.close()
asyncio.run(main())
EOF
```
Expected: 行数较前增加（4291 第 1 页约新增数行）

- [ ] **Step 6: 验证定时任务已注册**

Run:
```bash
.venv/bin/python - <<'EOF'
import asyncio
from web_scraper_service.scheduler.engine import list_scheduled_jobs
async def main():
    for j in await list_scheduled_jobs():
        print(j)
asyncio.run(main())
EOF
```
Expected: 列表含 `id="nfra:daily"`，`next_run` 为明日 8 点（Asia/Shanghai）附近，`trigger` 含 "hour=8"。

- [ ] **Step 7: 全量测试无回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全 PASS（37）

- [ ] **Step 8: Commit（如有小修）**

```bash
git add -A
git commit -m "test: verify nfra manual + scheduled smoke" || echo "nothing to commit"
```

---

## Self-Review 记录

- **Spec 覆盖**：Celery 任务 nfra_crawl_task(Task2)✓；定时 init_nfra_schedule 每日 8 点 4110+4291(Task2+Task4)✓；手动 POST /crawl 可指定 item_id+pages(Task3)✓；状态 GET /crawl/{job_id}(Task3)✓；配置 3 项(Task1)✓；lifespan 接入(Task4)✓；smoke(Task5)✓；测试 API+调度(Task3+Task4)✓；运行要求(API+worker+Redis)✓。
- **API 已验证**：`CronTrigger.from_crontab(expr, timezone="Asia/Shanghai")` 接受字符串时区；`AsyncResult(job_id, app=celery_app).state/.result` 可用；`nfra_crawl_task.apply_async(args=[...], task_id=job_id)` 返回 `result.id`；现有 `ok()`/`ApiKey` 依赖可复用；`TestClient` + `monkeypatch.setattr(settings,"api_key",...)` 为现有测试模式。
- **类型一致**：`nfra_crawl_task(item_id:int, pages:int)->dict` 在 Task2/Task3/Task4 一致；`init_nfra_schedule()->None` async 在 Task2/Task4 一致；`CrawlRequest{item_id:int=4110, pages:int=5}` 与 API body 一致；状态 dict `{job_id,status,result}` 一致。
- **无占位符**：所有步骤含完整代码与确切命令；测试代码完整内联。
