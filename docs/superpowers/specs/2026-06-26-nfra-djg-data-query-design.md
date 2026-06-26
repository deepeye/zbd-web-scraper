# djg_data 查询接口 — 设计文档

## 概述

新增 API 接口，按采集时间范围（`crawl_time`）查询 `zbd_crawler_data.djg_data` 表数据，支持翻页。加到现有 `api/v1/nfra.py` 路由下。

## 接口

`GET /api/v1/nfra/data`

- Query 参数：
  - `start_date: datetime | None` — crawl_time 下界（含），默认 None。
  - `end_date: datetime | None` — crawl_time 上界（含），默认 None。
  - 复用现有 `Pagination` 依赖（page≥1，size 1-100 默认 20）。
- 过滤：`crawl_time >= start_date AND crawl_time <= end_date`（两端可选；省略=不限制）。与 `results.py` 一致用 `>=` / `<=` 闭区间。
- 排序：`crawl_time DESC, id DESC`（最新采集在前）。
- 响应：`ok(rows, PaginationMeta)`，每行字段：`id`(str)、`doc_id`、`issue_date`、`issuing_authority`、`doc_number`、`institution_name`、`person_name`、`position`、`doc_title`、`doc_url`、`crawl_time`(str)。
- 需 `X-API-Key`。

示例：`GET /api/v1/nfra/data?start_date=2026-06-25T00:00:00&end_date=2026-06-26T00:00:00&page=1&size=20`

## 关键点：独立库数据源

djg_data 在 `zbd_crawler_data` 库，经 `SnapshotSession`（`storage/snapshot.py` 的 async_sessionmaker）访问，**不是**主库 `DbSession`。现有 `deps.py` 的 repo 依赖都绑主库 `scraper_db`，不能复用。故新增依赖：
- `deps.py` 加 `async def get_snapshot_session()`（`async with SnapshotSession() as s: yield s`）。
- `get_djg_data_repo(session: SnapshotSessionD) -> DjgDataRepo`。
- `DjgDataRepoD = Annotated[DjgDataRepo, Depends(get_djg_data_repo)]`。
- `SnapshotSessionD = Annotated[AsyncSession, Depends(get_snapshot_session)]`（供后续复用）。

## 组件

### 1. `storage/djg_data.py`（修改）

`DjgDataRepo` 加两方法：

```python
async def list_by_crawl_time(
    self,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[DjgData]:
    stmt = select(DjgData)
    if start_date:
        stmt = stmt.where(DjgData.crawl_time >= start_date)
    if end_date:
        stmt = stmt.where(DjgData.crawl_time <= end_date)
    stmt = stmt.order_by(DjgData.crawl_time.desc(), DjgData.id.desc()).limit(limit).offset(offset)
    result = await self.session.execute(stmt)
    return list(result.scalars().all())

async def count_by_crawl_time(
    self,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> int:
    stmt = select(func.count()).select_from(DjgData)
    if start_date:
        stmt = stmt.where(DjgData.crawl_time >= start_date)
    if end_date:
        stmt = stmt.where(DjgData.crawl_time <= end_date)
    result = await self.session.execute(stmt)
    return result.scalar_one()
```

注：`func` 与 `select` 已在 djg_data.py 顶部导入。

### 2. `api/deps.py`（修改）

加 snapshot-session 与 djg-data-repo 依赖：

```python
from web_scraper_service.storage.snapshot import SnapshotSession
from web_scraper_service.storage.djg_data import DjgDataRepo

async def get_snapshot_session() -> AsyncGenerator[AsyncSession, None]:
    async with SnapshotSession() as session:
        yield session

SnapshotSessionD = Annotated[AsyncSession, Depends(get_snapshot_session)]

def get_djg_data_repo(session: SnapshotSessionD) -> DjgDataRepo:
    return DjgDataRepo(session)

DjgDataRepoD = Annotated[DjgDataRepo, Depends(get_djg_data_repo)]
```

注：`AsyncGenerator`、`AsyncSession`、`Depends`、`Annotated` 已在 deps.py 顶部导入（`get_db` 用了同模式）。

### 3. `api/v1/nfra.py`（修改）

加 GET /data 路由：

```python
from datetime import datetime
from web_scraper_service.api.deps import ApiKey, DjgDataRepoD, Pagination
from web_scraper_service.api.response import PaginationMeta, ok

@router.get("/data")
async def list_data(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    repo: DjgDataRepoD = None,
    _: ApiKey = None,
    pagination: Pagination = None,
) -> dict[str, Any]:
    rows = await repo.list_by_crawl_time(
        start_date=start_date, end_date=end_date,
        limit=pagination.size, offset=pagination.offset,
    )
    total = await repo.count_by_crawl_time(start_date=start_date, end_date=end_date)
    return ok(
        [
            {
                "id": str(r.id), "doc_id": r.doc_id,
                "issue_date": r.issue_date, "issuing_authority": r.issuing_authority,
                "doc_number": r.doc_number, "institution_name": r.institution_name,
                "person_name": r.person_name, "position": r.position,
                "doc_title": r.doc_title, "doc_url": r.doc_url,
                "crawl_time": str(r.crawl_time),
            }
            for r in rows
        ],
        PaginationMeta(page=pagination.page, size=pagination.size, total=total),
    )
```

注：路由参数风格（`repo=None`、`_: ApiKey = None`、`pagination: Pagination = None`）与现有 `results.py` `list_results` 一致。

### 4. `tests/test_api/test_nfra.py`（修改）

加 GET /data 测试（mock `DjgDataRepo` via 依赖覆盖）：
- 日期范围过滤返回行 + 分页 meta（total 正确）。
- 空结果（start>end）返回 `{"data": [], pagination}`。
- 翻页（page=2/size）offset 正确。
- 未带 API key → 401。

测试用 FastAPI `dependency_overrides` 覆盖 `get_djg_data_repo` 返回 mock repo，避免连真实库。

## 错误处理

| 场景 | 处理 |
|------|------|
| 参数非法（page<1/size 越界） | Pagination 现有 422 |
| 未带 API key | 401 |
| 日期范围无效（start>end） | 返回空（不报错，与 results.py 一致） |
| 库连接失败 | 500（FastAPI 默认） |

## 改动文件清单

| 文件 | 动作 |
|------|------|
| `src/web_scraper_service/storage/djg_data.py` | 修改 — 加 list_by_crawl_time/count_by_crawl_time |
| `src/web_scraper_service/api/deps.py` | 修改 — 加 SnapshotSession/DjgDataRepo 依赖 |
| `src/web_scraper_service/api/v1/nfra.py` | 修改 — 加 GET /data 路由 |
| `tests/test_api/test_nfra.py` | 修改 — 加 GET /data 测试 |

## 注意事项

- 数据源是独立库 `zbd_crawler_data`，经 `SnapshotSession`；不复用主库 `DbSession`。
- 排序 `crawl_time DESC, id DESC` 保证最新采集在前、同时间稳定排序。
- 日期闭区间（`>=` / `<=`）。
- 复用现有 `Pagination`/`PaginationMeta`/`ok`/`ApiKey`，与 `results.py` 风格一致。
- `crawl_time` 是 `timestamptz`；返回 `str(r.crawl_time)` 保持与现有时间字段序列化一致。
