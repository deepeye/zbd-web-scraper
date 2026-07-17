# NFRA publish_date 默认排序实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将三个 NFRA 数据查询接口（djg/capital/equity）的默认排序与日期过滤从 `crawl_time` 改为 `publish_date`，`NULL` 值排在末尾。

**Architecture:** 重命名三个仓库的 `list_by_crawl_time`/`count_by_crawl_time` 为 `list_by_publish_date`/`count_by_publish_date`，修改 SQL 过滤/排序条件，同步更新 API 调用方、测试与文档。

**Tech Stack:** Python 3.x, FastAPI, SQLAlchemy 2.x, pytest, PostgreSQL

## Global Constraints

- 三个仓库方法只在 `src/web_scraper_service/api/v1/nfra.py` 的 `/data` 接口中使用，可直接重命名，无外部调用者。
- 排序：`publish_date DESC NULLS LAST, id DESC`。
- 过滤：`start_date`/`end_date` 改为作用于 `publish_date`。
- 分页参数 `page`/`size` 由 `Pagination` 依赖统一校验，保持不变。
- 数据库索引通过 `init_*_table()` 中的 `CREATE INDEX IF NOT EXISTS` 添加，与现有建表方式一致。

---

### Task 1: Update `DjgDataRepo` in `storage/djg_data.py`

**Files:**
- Modify: `src/web_scraper_service/storage/djg_data.py:62-107`
- Test: `tests/test_api/test_nfra.py:288-370`

**Interfaces:**
- Consumes: `DjgData.publish_date` (existing `Date` column)
- Produces: `DjgDataRepo.list_by_publish_date(start_date, end_date, limit, offset)` and `DjgDataRepo.count_by_publish_date(start_date, end_date)`

- [ ] **Step 1: Rename and rewrite `list_by_crawl_time`**

```python
    async def list_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[DjgData]:
        """按 publish_date 范围查询，最新发布在前，NULL 置后。"""
        stmt = select(DjgData)
        if start_date is not None:
            stmt = stmt.where(DjgData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(DjgData.publish_date <= end_date)
        stmt = (
            stmt.order_by(DjgData.publish_date.desc().nulls_last(), DjgData.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
```

- [ ] **Step 2: Rename and rewrite `count_by_crawl_time`**

```python
    async def count_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> int:
        """按 publish_date 范围计数。"""
        stmt = select(func.count()).select_from(DjgData)
        if start_date is not None:
            stmt = stmt.where(DjgData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(DjgData.publish_date <= end_date)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
```

- [ ] **Step 3: Add `publish_date` index in `init_djg_table`**

```python
async def init_djg_table() -> None:
    """CREATE TABLE IF NOT EXISTS djg_data."""
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_DjgBase.metadata.create_all)
        await conn.execute(text("ALTER TABLE djg_data ADD COLUMN IF NOT EXISTS publish_date DATE"))
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_djg_data_publish_date "
                "ON djg_data (publish_date DESC NULLS LAST)"
            )
        )
```

- [ ] **Step 4: Run djg_data related tests**

Run: `pytest tests/test_api/test_nfra.py -k "djg" -v`
Expected: 当前会失败，因为 API 仍调用旧方法名（Task 4 修复）。

- [ ] **Step 5: Commit**

```bash
git add src/web_scraper_service/storage/djg_data.py
git commit -m "refactor(storage): rename DjgDataRepo query to list_by_publish_date"
```

---

### Task 2: Update `CapitalChangeDataRepo` in `storage/capital_change_data.py`

**Files:**
- Modify: `src/web_scraper_service/storage/capital_change_data.py:67-109`

**Interfaces:**
- Produces: `CapitalChangeDataRepo.list_by_publish_date(...)` and `CapitalChangeDataRepo.count_by_publish_date(...)`

- [ ] **Step 1: Rename and rewrite `list_by_crawl_time`**

```python
    async def list_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[CapitalChangeData]:
        """按 publish_date 范围查询，最新发布在前，NULL 置后。"""
        stmt = select(CapitalChangeData)
        if start_date is not None:
            stmt = stmt.where(CapitalChangeData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(CapitalChangeData.publish_date <= end_date)
        stmt = (
            stmt.order_by(CapitalChangeData.publish_date.desc().nulls_last(), CapitalChangeData.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
```

- [ ] **Step 2: Rename and rewrite `count_by_crawl_time`**

```python
    async def count_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> int:
        """按 publish_date 范围计数。"""
        stmt = select(func.count()).select_from(CapitalChangeData)
        if start_date is not None:
            stmt = stmt.where(CapitalChangeData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(CapitalChangeData.publish_date <= end_date)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
```

- [ ] **Step 3: Add `publish_date` index in `init_capital_change_table`**

```python
async def init_capital_change_table() -> None:
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_CapitalChangeBase.metadata.create_all)
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_capital_change_data_publish_date "
                "ON capital_change_data (publish_date DESC NULLS LAST)"
            )
        )
```

- [ ] **Step 4: Commit**

```bash
git add src/web_scraper_service/storage/capital_change_data.py
git commit -m "refactor(storage): rename CapitalChangeDataRepo query to list_by_publish_date"
```

---

### Task 3: Update `EquityChangeDataRepo` in `storage/equity_change_data.py`

**Files:**
- Modify: `src/web_scraper_service/storage/equity_change_data.py:72-114`

**Interfaces:**
- Produces: `EquityChangeDataRepo.list_by_publish_date(...)` and `EquityChangeDataRepo.count_by_publish_date(...)`

- [ ] **Step 1: Rename and rewrite `list_by_crawl_time`**

```python
    async def list_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[EquityChangeData]:
        """按 publish_date 范围查询，最新发布在前，NULL 置后。"""
        stmt = select(EquityChangeData)
        if start_date is not None:
            stmt = stmt.where(EquityChangeData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(EquityChangeData.publish_date <= end_date)
        stmt = (
            stmt.order_by(EquityChangeData.publish_date.desc().nulls_last(), EquityChangeData.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
```

- [ ] **Step 2: Rename and rewrite `count_by_crawl_time`**

```python
    async def count_by_publish_date(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> int:
        """按 publish_date 范围计数。"""
        stmt = select(func.count()).select_from(EquityChangeData)
        if start_date is not None:
            stmt = stmt.where(EquityChangeData.publish_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(EquityChangeData.publish_date <= end_date)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
```

- [ ] **Step 3: Add `publish_date` index in `init_equity_change_table`**

```python
async def init_equity_change_table() -> None:
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_EquityChangeBase.metadata.create_all)
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_equity_change_data_publish_date "
                "ON equity_change_data (publish_date DESC NULLS LAST)"
            )
        )
```

- [ ] **Step 4: Commit**

```bash
git add src/web_scraper_service/storage/equity_change_data.py
git commit -m "refactor(storage): rename EquityChangeDataRepo query to list_by_publish_date"
```

---

### Task 4: Update API route calls in `api/v1/nfra.py`

**Files:**
- Modify: `src/web_scraper_service/api/v1/nfra.py:131-168`, `212-253`, `256-290`

**Interfaces:**
- Consumes: `repo.list_by_publish_date(...)` and `repo.count_by_publish_date(...)`

- [ ] **Step 1: Update `/capital/data` handler**

```python
@router.get("/capital/data")
async def list_capital_data(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    repo: CapitalChangeDataRepoD = None,  # type: ignore[assignment]
    _: ApiKey = None,  # type: ignore[assignment]
    pagination: Pagination = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    rows = await repo.list_by_publish_date(
        start_date=start_date,
        end_date=end_date,
        limit=pagination.size,
        offset=pagination.offset,
    )
    total = await repo.count_by_publish_date(start_date=start_date, end_date=end_date)
    ...
```

- [ ] **Step 2: Update `/equity/data` handler**

```python
    rows = await repo.list_by_publish_date(
        start_date=start_date,
        end_date=end_date,
        limit=pagination.size,
        offset=pagination.offset,
    )
    total = await repo.count_by_publish_date(start_date=start_date, end_date=end_date)
```

- [ ] **Step 3: Update `/djg/data` handler**

```python
    rows = await repo.list_by_publish_date(
        start_date=start_date,
        end_date=end_date,
        limit=pagination.size,
        offset=pagination.offset,
    )
    total = await repo.count_by_publish_date(start_date=start_date, end_date=end_date)
```

- [ ] **Step 4: Commit**

```bash
git add src/web_scraper_service/api/v1/nfra.py
git commit -m "refactor(api): use list_by_publish_date for nfra data endpoints"
```

---

### Task 5: Update tests in `tests/test_api/test_nfra.py`

**Files:**
- Modify: `tests/test_api/test_nfra.py`

**Interfaces:**
- Consumes: `list_by_publish_date` and `count_by_publish_date` mocks

- [ ] **Step 1: Update djg data test mocks**

将 `test_get_data_with_date_range`、`test_get_data_publish_date_null`、`test_get_data_empty`、`test_get_data_pagination_offset` 中的：

```python
repo.list_by_crawl_time = AsyncMock(...)
repo.count_by_crawl_time = AsyncMock(...)
```

改为：

```python
repo.list_by_publish_date = AsyncMock(...)
repo.count_by_publish_date = AsyncMock(...)
```

- [ ] **Step 2: Update capital data test mocks**

在 `test_get_capital_data` 中，将：

```python
repo.list_by_crawl_time = AsyncMock(return_value=[_fake_capital_row()])
repo.count_by_crawl_time = AsyncMock(return_value=1)
```

改为：

```python
repo.list_by_publish_date = AsyncMock(return_value=[_fake_capital_row()])
repo.count_by_publish_date = AsyncMock(return_value=1)
```

- [ ] **Step 3: Update equity data test mocks**

在 `test_get_equity_data` 中，将：

```python
repo.list_by_crawl_time = AsyncMock(return_value=[_fake_equity_row()])
repo.count_by_crawl_time = AsyncMock(return_value=1)
```

改为：

```python
repo.list_by_publish_date = AsyncMock(return_value=[_fake_equity_row()])
repo.count_by_publish_date = AsyncMock(return_value=1)
```

- [ ] **Step 4: Run all nfra API tests**

Run: `pytest tests/test_api/test_nfra.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_api/test_nfra.py
git commit -m "test(api): update nfra data tests for publish_date queries"
```

---

### Task 6: Update `docs/API.md`

**Files:**
- Modify: `docs/API.md` (sections 7.3, 7.4, 7.5)

- [ ] **Step 1: Update `GET /api/v1/nfra/djg/data` section**

将参数说明改为：

```markdown
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `start_date` | datetime | null | `publish_date >= start_date`（含） |
| `end_date` | datetime | null | `publish_date <= end_date`（含） |
| `page` | int | 1 | 页码 |
| `size` | int | 20 | 每页数（1–100） |

排序：`publish_date DESC NULLS LAST, id DESC`（最新发布在前，`publish_date` 为空排最后）。`start_date > end_date` 返回空（不报错）。
```

- [ ] **Step 2: Update `GET /api/v1/nfra/capital/data` section**

将排序说明由 `crawl_time DESC, id DESC` 改为：

```markdown
排序 `publish_date DESC NULLS LAST, id DESC`，参数 `start_date`/`end_date` 过滤 `publish_date`。
```

- [ ] **Step 3: Update `GET /api/v1/nfra/equity/data` section**

同上，添加排序/过滤说明。

- [ ] **Step 4: Commit**

```bash
git add docs/API.md
git commit -m "docs(api): document nfra data endpoints publish_date sort and filter"
```

---

## Self-Review

**Spec coverage:**
- [x] 三个接口默认按 `publish_date` 倒序：Task 1/2/3。
- [x] `start_date`/`end_date` 改为过滤 `publish_date`：Task 1/2/3。
- [x] `NULL publish_date` 排最后：Task 1/2/3 使用 `nulls_last()`。
- [x] 同日期按 `id DESC` 稳定 tie-breaker：Task 1/2/3。
- [x] API 调用方更新：Task 4。
- [x] 测试更新：Task 5。
- [x] 文档更新：Task 6。
- [x] 索引建议：Task 1/2/3 通过 `init_*_table` 添加。

**Placeholder scan:** 无 TBD/TODO/"implement later"。

**Type consistency:** 所有方法签名保持 `start_date: datetime | None`, `end_date: datetime | None`, `limit: int`, `offset: int`，与现有 API 一致。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-17-nfra-publish-date-sort.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach would you like?
