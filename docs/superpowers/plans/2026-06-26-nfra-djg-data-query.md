# djg_data 查询接口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `GET /api/v1/nfra/data` 接口，按 `crawl_time` 范围查询 `zbd_crawler_data.djg_data`，翻页返回。

**Architecture:** `DjgDataRepo` 加 `list_by_crawl_time`/`count_by_crawl_time`（经 `SnapshotSession` 访问独立库）；`deps.py` 加 `DjgDataRepoD` 依赖；`api/v1/nfra.py` 加 `GET /data` 路由，复用 `Pagination`/`PaginationMeta`/`ok`/`ApiKey`，与 `results.py` 风格一致。

**Tech Stack:** FastAPI · SQLAlchemy 2.x async · pytest (TestClient + dependency_overrides)

## Global Constraints

- 数据源是独立库 `zbd_crawler_data`，经 `SnapshotSession`（`storage/snapshot.py`），**不复用**主库 `DbSession`。
- 过滤 `crawl_time >= start_date AND crawl_time <= end_date`（闭区间，两端可选）。
- 排序 `crawl_time DESC, id DESC`。
- 复用现有 `Pagination`（page≥1, size 1-100 默认 20）、`PaginationMeta`、`ok`、`ApiKey`。
- 需 `X-API-Key`。
- mypy strict 全局开启；代码须过 mypy。venv 在 `.venv`。
- 约束引自 spec：`docs/superpowers/specs/2026-06-26-nfra-djg-data-query-design.md`。

## File Structure

| 文件 | 职责 |
|------|------|
| `src/web_scraper_service/storage/djg_data.py` | 加 list_by_crawl_time/count_by_crawl_time |
| `src/web_scraper_service/api/deps.py` | 加 get_snapshot_session/SnapshotSessionD/get_djg_data_repo/DjgDataRepoD |
| `src/web_scraper_service/api/v1/nfra.py` | 加 GET /data 路由 |
| `tests/test_api/test_nfra.py` | 加 GET /data 测试 |

---

### Task 1: 存储层 + 依赖注入

**Files:**
- Modify: `src/web_scraper_service/storage/djg_data.py`（DjgDataRepo 加两方法）
- Modify: `src/web_scraper_service/api/deps.py`（加 snapshot-session 与 djg-data-repo 依赖）

**Interfaces:**
- Consumes: `SnapshotSession`（`storage/snapshot.py`）、`DjgData` 模型、`func`/`select`（已在 djg_data.py 顶部导入）
- Produces:
  - `DjgDataRepo.list_by_crawl_time(start_date, end_date, limit, offset) -> list[DjgData]`
  - `DjgDataRepo.count_by_crawl_time(start_date, end_date) -> int`
  - `deps.get_snapshot_session()`、`deps.SnapshotSessionD`、`deps.get_djg_data_repo()`、`deps.DjgDataRepoD`

- [ ] **Step 1: djg_data.py 加两方法**

在 `src/web_scraper_service/storage/djg_data.py` 的 `DjgDataRepo` 类中，`existing_doc_ids` 方法之后、`insert_many` 之前插入：

```python
    async def list_by_crawl_time(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[DjgData]:
        """按 crawl_time 范围查询，最新采集在前。"""
        stmt = select(DjgData)
        if start_date is not None:
            stmt = stmt.where(DjgData.crawl_time >= start_date)
        if end_date is not None:
            stmt = stmt.where(DjgData.crawl_time <= end_date)
        stmt = stmt.order_by(DjgData.crawl_time.desc(), DjgData.id.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_crawl_time(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> int:
        """按 crawl_time 范围计数。"""
        stmt = select(func.count()).select_from(DjgData)
        if start_date is not None:
            stmt = stmt.where(DjgData.crawl_time >= start_date)
        if end_date is not None:
            stmt = stmt.where(DjgData.crawl_time <= end_date)
        result = await self.session.execute(stmt)
        return result.scalar_one()
```

注：`datetime`、`func`、`select` 已在 djg_data.py 顶部导入（`from datetime import datetime`、`from sqlalchemy import ... func, select`）。

- [ ] **Step 2: deps.py 加依赖**

在 `src/web_scraper_service/api/deps.py` 顶部 import 段，把
`from web_scraper_service.storage.repositories import ItemRepo, JobRepo, MetricsRepo, SpiderRepo`
之后追加一行：

```python
from web_scraper_service.storage.djg_data import DjgDataRepo
from web_scraper_service.storage.snapshot import SnapshotSession
```

然后在文件末尾（`Pagination = Annotated[PaginationParams, Depends()]` 之后）追加：

```python


# ── Snapshot DB (独立库 zbd_crawler_data) ──────────────────
async def get_snapshot_session() -> AsyncSession:
    async with SnapshotSession() as session:
        yield session


SnapshotSessionD = Annotated[AsyncSession, Depends(get_snapshot_session)]


def get_djg_data_repo(session: SnapshotSessionD) -> DjgDataRepo:
    return DjgDataRepo(session)


DjgDataRepoD = Annotated[DjgDataRepo, Depends(get_djg_data_repo)]
```

注：`Annotated`、`AsyncSession`、`Depends` 已在 deps.py 顶部导入。`get_snapshot_session` 与现有 `get_db`（`async for session in get_session(): yield session`）同模式。

- [ ] **Step 3: 验证可导入**

Run: `.venv/bin/python -c "from web_scraper_service.api.deps import DjgDataRepoD, SnapshotSessionD, get_djg_data_repo; from web_scraper_service.storage.djg_data import DjgDataRepo; print('ok'); print(hasattr(DjgDataRepo,'list_by_crawl_time'), hasattr(DjgDataRepo,'count_by_crawl_time'))"`
Expected: `ok` 与 `True True`

- [ ] **Step 4: ruff + mypy**

Run: `.venv/bin/ruff check src/web_scraper_service/storage/djg_data.py src/web_scraper_service/api/deps.py && .venv/bin/python -m mypy src/web_scraper_service/storage/djg_data.py src/web_scraper_service/api/deps.py`
Expected: 无 error（deps.py 有预存 unused import `UUID`/`SpiderNotFoundError`/`Query` 等 ruff F401 —— 非本任务引入，不动；mypy 可能报预存项，记录但非本任务引入则可接受）

- [ ] **Step 5: Commit**

```bash
git add src/web_scraper_service/storage/djg_data.py src/web_scraper_service/api/deps.py
git commit -m "feat: add djg_data list/count by crawl_time + deps"
```

---

### Task 2: GET /data 路由 — TDD

**Files:**
- Modify: `src/web_scraper_service/api/v1/nfra.py`（加 GET /data 路由）
- Modify: `tests/test_api/test_nfra.py`（加 GET /data 测试）

**Interfaces:**
- Consumes: Task 1 的 `DjgDataRepoD`、`Pagination`、`PaginationMeta`、`ok`、`ApiKey`
- Produces: `GET /api/v1/nfra/data` 路由

- [ ] **Step 1: 写失败测试（追加到 tests/test_api/test_nfra.py）**

在文件末尾追加（保留现有 import；如缺 `AsyncSession`/`datetime`/`MagicMock` 则在文件顶部补 import）：

```python
from datetime import datetime, timezone

from web_scraper_service.api.deps import get_djg_data_repo
from web_scraper_service.storage.djg_data import DjgData


def _fake_row(
    *, id: int = 1, doc_id: int = 1258343, person_name: str = "张伟", position: str = "董事",
    institution_name: str = "苏州银行股份有限公司", issue_date: str = "2026年5月14日",
    issuing_authority: str = "江苏金融监管局", doc_number: str = "苏金复〔2026〕139号",
    doc_title: str = "江苏金融监管局关于张伟等6人苏州银行董事、独立董事任职资格的批复",
    doc_url: str = "https://www.nfra.gov.cn/x",
    crawl_time: datetime | None = None,
) -> DjgData:
    r = DjgData()
    r.id = id
    r.doc_id = doc_id
    r.person_name = person_name
    r.position = position
    r.institution_name = institution_name
    r.issue_date = issue_date
    r.issuing_authority = issuing_authority
    r.doc_number = doc_number
    r.doc_title = doc_title
    r.doc_url = doc_url
    r.crawl_time = crawl_time or datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)
    return r


def test_get_data_with_date_range(client: TestClient, _api_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = MagicMock()
    repo.list_by_crawl_time = AsyncMock(return_value=[_fake_row(id=1), _fake_row(id=2)])
    repo.count_by_crawl_time = AsyncMock(return_value=2)
    client.app.dependency_overrides[get_djg_data_repo] = lambda: repo
    try:
        resp = client.get(
            "/api/v1/nfra/data",
            params={"start_date": "2026-06-25T00:00:00", "end_date": "2026-06-26T00:00:00", "page": 1, "size": 20},
            headers={"X-API-Key": _api_key},
        )
    finally:
        client.app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["length"] if isinstance(body["data"], list) else True  # noqa: E712
    rows = body["data"]
    assert len(rows) == 2
    assert rows[0]["person_name"] == "张伟"
    assert rows[0]["doc_id"] == 1258343
    assert "crawl_time" in rows[0]
    assert body["pagination"]["total"] == 2
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["size"] == 20
    repo.list_by_crawl_time.assert_awaited_once()
    args, kwargs = repo.list_by_crawl_time.call_args
    assert kwargs["limit"] == 20
    assert kwargs["offset"] == 0


def test_get_data_empty(client: TestClient, _api_key: str) -> None:
    repo = MagicMock()
    repo.list_by_crawl_time = AsyncMock(return_value=[])
    repo.count_by_crawl_time = AsyncMock(return_value=0)
    client.app.dependency_overrides[get_djg_data_repo] = lambda: repo
    try:
        resp = client.get("/api/v1/nfra/data", headers={"X-API-Key": _api_key})
    finally:
        client.app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["data"] == []
    assert resp.json()["pagination"]["total"] == 0


def test_get_data_pagination_offset(client: TestClient, _api_key: str) -> None:
    repo = MagicMock()
    repo.list_by_crawl_time = AsyncMock(return_value=[_fake_row(id=21)])
    repo.count_by_crawl_time = AsyncMock(return_value=40)
    client.app.dependency_overrides[get_djg_data_repo] = lambda: repo
    try:
        resp = client.get(
            "/api/v1/nfra/data",
            params={"page": 2, "size": 20},
            headers={"X-API-Key": _api_key},
        )
    finally:
        client.app.dependency_overrides.clear()
    assert resp.status_code == 200
    _, kwargs = repo.list_by_crawl_time.call_args
    assert kwargs["offset"] == 20  # (page2-1)*size20


def test_get_data_no_api_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "api_key", "test-key")
    resp = client.get("/api/v1/nfra/data")
    assert resp.status_code == 401
```

注：`test_get_data_with_date_range` 第一行 assert 用了一个稍绕的写法，可简化为直接 `rows = resp.json()["data"]; assert len(rows) == 2`（删除那条 noqa 行）。实现者请用简化版：

```python
    body = resp.json()
    rows = body["data"]
    assert len(rows) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_api/test_nfra.py::test_get_data_with_date_range tests/test_api/test_nfra.py::test_get_data_empty -v`
Expected: FAIL（`GET /api/v1/nfra/data` 返回 404，路由未实现）

- [ ] **Step 3: 写实现 — nfra.py 加 GET /data 路由**

在 `src/web_scraper_service/api/v1/nfra.py`：
- 顶部 import 段把 `from web_scraper_service.api.deps import ApiKey` 改为：
  ```python
  from datetime import datetime
  ```
  并在该行之后追加：
  ```python
  from web_scraper_service.api.deps import ApiKey, DjgDataRepoD, Pagination
  from web_scraper_service.api.response import PaginationMeta, ok
  ```
  （`ok` 若已导入则不重复；当前 nfra.py 未导入 `ok`/`PaginationMeta`/`Pagination`/`DjgDataRepoD`/`datetime`，均需新增。）

- 在文件末尾（`crawl_status` 路由之后）追加：

```python
@router.get("/data")
async def list_data(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    repo: DjgDataRepoD = None,
    _: ApiKey = None,
    pagination: Pagination = None,
) -> dict[str, Any]:
    rows = await repo.list_by_crawl_time(
        start_date=start_date,
        end_date=end_date,
        limit=pagination.size,
        offset=pagination.offset,
    )
    total = await repo.count_by_crawl_time(start_date=start_date, end_date=end_date)
    return ok(
        [
            {
                "id": str(r.id),
                "doc_id": r.doc_id,
                "issue_date": r.issue_date,
                "issuing_authority": r.issuing_authority,
                "doc_number": r.doc_number,
                "institution_name": r.institution_name,
                "person_name": r.person_name,
                "position": r.position,
                "doc_title": r.doc_title,
                "doc_url": r.doc_url,
                "crawl_time": str(r.crawl_time),
            }
            for r in rows
        ],
        PaginationMeta(page=pagination.page, size=pagination.size, total=total),
    )
```

注：路由参数风格（`repo: DjgDataRepoD = None`、`_: ApiKey = None`、`pagination: Pagination = None`）与现有 `results.py` `list_results` 完全一致。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api/test_nfra.py -v`
Expected: 全 PASS（既有 8 + 新 4 = 12；含 GET /data 4 项）

- [ ] **Step 5: ruff + mypy + 全量回归**

Run: `.venv/bin/ruff check src/web_scraper_service/api/v1/nfra.py tests/test_api/test_nfra.py && .venv/bin/python -m mypy src/web_scraper_service/api/v1/nfra.py && .venv/bin/python -m pytest -q`
Expected: ruff/mypy 无 error（nfra.py 预存项除外）；全量 PASS

- [ ] **Step 6: Commit**

```bash
git add src/web_scraper_service/api/v1/nfra.py tests/test_api/test_nfra.py
git commit -m "feat: add GET /api/v1/nfra/data query with date range + pagination"
```

---

### Task 3: smoke 验收

**Files:** 无（仅运行验证）

**前置：** Docker DB + Redis 在跑；`zbd_crawler_data` 库存在且 `djg_data` 有数据（前序 smoke 已写入 18 行）；`API_KEY` 在 `.env`。

- [ ] **Step 1: 启动 API 服务**

Run: `make dev`（或后台 `nohup .venv/bin/uvicorn web_scraper_service.main:app --host 127.0.0.1 --port 8000 --log-level warning &`）

- [ ] **Step 2: 全量查询（无日期过滤）**

Run:
```bash
API_KEY=$(.venv/bin/python -c "from web_scraper_service.config import settings; print(settings.api_key)")
curl -s "http://127.0.0.1:8000/api/v1/nfra/data?page=1&size=5" -H "X-API-Key: $API_KEY" | head -c 600
```
Expected: 200，`data` 含最多 5 行，`pagination.total` ≈ 18（前序 smoke 写入数）

- [ ] **Step 3: 日期范围过滤**

Run:
```bash
curl -s "http://127.0.0.1:8000/api/v1/nfra/data?start_date=2026-06-25T00:00:00&end_date=2026-06-25T23:59:59&page=1&size=20" -H "X-API-Key: $API_KEY" | head -c 400
```
Expected: 200，返回 6/25 当天采集的行

- [ ] **Step 4: 翻页验证**

Run:
```bash
curl -s "http://127.0.0.1:8000/api/v1/nfra/data?page=2&size=10" -H "X-API-Key: $API_KEY" | .venv/bin/python -c "import sys,json; d=json.load(sys.stdin); print('page', d['pagination']['page'], 'returned', len(d['data']), 'total', d['pagination']['total'])"
```
Expected: page 2，返回行数 ≤ 10，total 一致

- [ ] **Step 5: 401 无 key**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/v1/nfra/data`
Expected: `401`

- [ ] **Step 6: 全量测试无回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全 PASS

- [ ] **Step 7: 停 API、Commit（如有小修）**

```bash
git add -A
git commit -m "test: verify djg_data query smoke" || echo "nothing to commit"
```

---

## Self-Review 记录

- **Spec 覆盖**：list_by_crawl_time/count_by_crawl_time(Task1)✓；deps DjgDataRepoD via SnapshotSession(Task1)✓；GET /data 路由(Task2)✓；日期闭区间 + crawl_time DESC, id DESC(Task1)✓；复用 Pagination/PaginationMeta/ok/ApiKey(Task2)✓；测试 4 项含日期/空/翻页/401(Task2)✓；smoke(Task3)✓；独立库不复用 DbSession(Task1)✓。
- **类型一致**：`list_by_crawl_time(start_date,end_date,limit,offset)->list[DjgData]`、`count_by_crawl_time(start_date,end_date)->int` 在 Task1/Task2 调用一致；`DjgDataRepoD` 在 Task1 定义/Task2 路由参数一致；GET /data 参数 `(start_date,end_date,repo,pagination)` 与 results.py list_results 风格一致。
- **API 已验证**：deps.py 已导入 `Annotated`/`Depends`/`AsyncSession`；`get_db` 用 `async for ... yield` 模式；results.py `list_results` 参数风格（`= None` 默认）；nfra.py 当前未导入 `datetime`/`Pagination`/`PaginationMeta`/`ok`/`DjgDataRepoD`，Task2 Step3 明确新增。
- **无占位符**：所有步骤含完整代码与确切命令；测试代码完整内联（含 fake_row 构造）。
