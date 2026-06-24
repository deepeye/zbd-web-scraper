# nfra.gov.cn 文档快照采集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 采集 nfra.gov.cn itemId=4110 栏目最新若干页文档列表，抓取每个文档详情接口原始响应，写入独立库 `zbd_crawler_data.web_snapshot`（跳过已存在 docId）。

**Architecture:** 两阶段混合采集——列表发现用 Scrapling `AsyncStealthySession`（浏览器执行 JS 生成会话 cookie，plain HTTP 会 404），详情抓取用 `AsyncFetcher`（普通 HTTP，详情接口不校验 cookie）。详情原始响应体存入 `web_snapshot.snapshot`。纯逻辑（URL 构造、列表 JSON 解析、skip 过滤）抽到可导入模块 `crawlers/nfra.py` 以便单测；`scripts/crawl_nfra.py` 为薄 CLI 入口。

**Tech Stack:** Python 3.12 · Scrapling 0.4.9（AsyncStealthySession / AsyncFetcher）· SQLAlchemy 2.x async + asyncpg · loguru · pytest/pytest-asyncio

## Global Constraints

- Scrapling 已锁定 `>=0.4.9`（pyproject.toml 已更新，venv 已 sync）。运行列表阶段前需 `scrapling install` 装好浏览器。
- 列表 API URL 含未编码逗号，保持原样：`data_itemId={itemId},pageIndex={page},pageSize=18.json`。
- 详情 API 不校验 cookie，plain HTTP 可用。
- `web_snapshot` 与主库 `scraper_db` 是两个独立数据库；`doc_id` 为主键；写入用 `ON CONFLICT (doc_id) DO NOTHING`。
- 现有测试无 DB fixture（纯 pytest），DB/网络层不做单测，靠手动 smoke 验收；纯逻辑走 TDD。
- 约束引用自 spec：`docs/superpowers/specs/2026-06-24-nfra-snapshot-crawler-design.md`。

## File Structure

| 文件 | 职责 |
|------|------|
| `src/web_scraper_service/config.py` | 加 `snapshot_database_url` 配置项 |
| `src/web_scraper_service/storage/snapshot.py` | 独立库 engine + `WebSnapshot` 模型 + `SnapshotRepo` + `init_table()` |
| `src/web_scraper_service/crawlers/__init__.py` | 空 package 标记 |
| `src/web_scraper_service/crawlers/nfra.py` | 纯逻辑（URL 构造/解析/过滤）+ 异步编排（列表浏览器/详情HTTP/写入） |
| `scripts/crawl_nfra.py` | 薄 CLI 入口，解析参数并调用 `crawlers.nfra.run_crawl` |
| `.env.example` | 加 `SNAPSHOT_DATABASE_URL` |
| `Makefile` | 加 `crawl-nfra` target |
| `tests/test_nfra/__init__.py` | 空 package 标记 |
| `tests/test_nfra/test_parse.py` | 纯逻辑单测 |

**对 spec 的一处细化**：spec 把"编排 + CLI"都放在 `scripts/crawl_nfra.py`。为实现可单测，把纯逻辑与异步编排抽到可导入模块 `src/web_scraper_service/crawlers/nfra.py`，`scripts/crawl_nfra.py` 降为薄入口。这是为满足"每个文件单一职责 + 可测试"的细化，不改变行为。

---

### Task 1: 配置项 snapshot_database_url

**Files:**
- Modify: `src/web_scraper_service/config.py`（在 S3 段之前新增 Snapshot DB 段）
- Modify: `.env.example`（在 Scrapling 段之前新增）

**Interfaces:**
- Produces: `settings.snapshot_database_url: str`（后续 Task 2 的 engine 依赖此值）

- [ ] **Step 1: 在 config.py 新增配置项**

在 `src/web_scraper_service/config.py` 的 `# ── S3 ──` 段之前插入：

```python
    # ── Snapshot DB (独立库，存爬取快照) ──────────────────
    snapshot_database_url: str = (
        f"postgresql+asyncpg://{postgres_user}:{postgres_password}"
        f"@{postgres_host}:{postgres_port}/zbd_crawler_data"
    )
```

注意：`postgres_user/password/host/port` 是同类属性，在此处尚未定义（它们定义在更靠前的 PostgreSQL 段）。`pydantic-settings` 在模型实例化时解析，属性引用在运行时求值，顺序无影响。确认这些字段名与现有 PostgreSQL 段一致（`postgres_user`、`postgres_password`、`postgres_host`、`postgres_port`）。

- [ ] **Step 2: 在 .env.example 新增条目**

在 `.env.example` 的 `# ── Scrapling / Fetcher ──` 段之前插入：

```
# ── Snapshot Database (独立库) ─────────────────────────────
# 默认从 PostgreSQL 凭据派生，仅库名固定为 zbd_crawler_data
# 显式设置可覆盖：postgresql+asyncpg://user:pass@host:port/zbd_crawler_data
SNAPSHOT_DATABASE_URL=
```

- [ ] **Step 3: 验证配置可加载**

Run: `.venv/bin/python -c "from web_scraper_service.config import settings; print(settings.snapshot_database_url)"`
Expected: 输出形如 `postgresql+asyncpg://scraper:scraper_secret@localhost:5432/zbd_crawler_data`

- [ ] **Step 4: Commit**

```bash
git add src/web_scraper_service/config.py .env.example
git commit -m "feat: add snapshot_database_url config for zbd_crawler_data"
```

---

### Task 2: 快照存储层 storage/snapshot.py

**Files:**
- Create: `src/web_scraper_service/storage/snapshot.py`

**Interfaces:**
- Consumes: `settings.snapshot_database_url`（Task 1）
- Produces:
  - `WebSnapshot`（SQLAlchemy 模型：`doc_id: int PK`、`snapshot: Text`、`crawl_time: DateTime`）
  - `async init_table() -> None`
  - `class SnapshotRepo`：`async existing_doc_ids(doc_ids: set[int]) -> set[int]`、`async insert_many(rows: list[dict]) -> int`
  - 模块级 `snapshot_engine`、`SnapshotSession`（async session factory）

- [ ] **Step 1: 创建 storage/snapshot.py**

写入 `src/web_scraper_service/storage/snapshot.py`：

```python
"""独立库 zbd_crawler_data 的快照存储：engine、模型、仓库。

与主库 scraper_db 完全独立，不走 Alembic；表由 init_table() 用
CREATE TABLE IF NOT EXISTS 创建。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Text, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from web_scraper_service.config import settings


class _SnapshotBase(DeclarativeBase):
    pass


class WebSnapshot(_SnapshotBase):
    __tablename__ = "web_snapshot"

    doc_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    crawl_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


snapshot_engine = create_async_engine(
    settings.snapshot_database_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
)

SnapshotSession = async_sessionmaker(
    snapshot_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_table() -> None:
    """CREATE TABLE IF NOT EXISTS web_snapshot."""
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_SnapshotBase.metadata.create_all)


class SnapshotRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def existing_doc_ids(self, doc_ids: set[int]) -> set[int]:
        """返回 doc_ids 中已存在于 web_snapshot 的 doc_id 集合。"""
        if not doc_ids:
            return set()
        stmt = select(WebSnapshot.doc_id).where(WebSnapshot.doc_id.in_(doc_ids))
        result = await self.session.execute(stmt)
        return {row[0] for row in result.all()}

    async def insert_many(self, rows: list[dict[str, Any]]) -> int:
        """批量插入，已存在 doc_id 跳过（ON CONFLICT DO NOTHING）。返回实际新增行数。"""
        if not rows:
            return 0
        stmt = pg_insert(WebSnapshot).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["doc_id"])
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount or 0


async def close_snapshot_engine() -> None:
    await snapshot_engine.dispose()
```

- [ ] **Step 2: 验证可导入且模型正确**

Run: `.venv/bin/python -c "from web_scraper_service.storage.snapshot import WebSnapshot, SnapshotRepo, init_table; print(WebSnapshot.__tablename__, [c.name for c in WebSnapshot.__table__.columns])"`
Expected: `web_snapshot ['doc_id', 'snapshot', 'crawl_time']`

- [ ] **Step 3: 类型检查**

Run: `.venv/bin/python -m mypy src/web_scraper_service/storage/snapshot.py` 2>&1 | tail -5
Expected: 无 error（warning 可接受；如报 `pg_insert` 相关错，确认 `sqlalchemy[asyncio]` 已装——已在 pyproject 依赖中）

- [ ] **Step 4: Commit**

```bash
git add src/web_scraper_service/storage/snapshot.py
git commit -m "feat: add snapshot storage layer for zbd_crawler_data.web_snapshot"
```

---

### Task 3: 纯逻辑（URL 构造 / 列表解析 / skip 过滤）— TDD

**Files:**
- Create: `src/web_scraper_service/crawlers/__init__.py`
- Create: `src/web_scraper_service/crawlers/nfra.py`
- Create: `tests/test_nfra/__init__.py`
- Create: `tests/test_nfra/test_parse.py`

**Interfaces:**
- Produces（本 task 仅纯函数）:
  - `build_list_url(item_id: int, page: int, page_size: int = 18) -> str`
  - `build_detail_url(doc_id: int) -> str`
  - `build_list_html_url(item_id: int) -> str`
  - `parse_doc_ids(body: str | bytes) -> list[int]`（解析列表 JSON，提取 docId；rptCode!=200 或无 rows 返回 []）
  - `filter_pending(doc_ids: list[int], existing: set[int]) -> list[int]`（去列表内重 + 去已存在，保序）

- [ ] **Step 1: 创建 package 标记文件**

创建空文件 `src/web_scraper_service/crawlers/__init__.py` 与 `tests/test_nfra/__init__.py`。

- [ ] **Step 2: 写失败测试 tests/test_nfra/test_parse.py**

```python
"""nfra 采集纯逻辑单测（不打网络）。"""

from __future__ import annotations

from web_scraper_service.crawlers.nfra import (
    build_detail_url,
    build_list_html_url,
    build_list_url,
    filter_pending,
    parse_doc_ids,
)


def test_build_list_url() -> None:
    assert build_list_url(4110, 1) == (
        "https://www.nfra.gov.cn/cn/static/data/DocInfo/"
        "SelectDocByItemIdAndChild/data_itemId=4110,pageIndex=1,pageSize=18.json"
    )
    assert build_list_url(4110, 3, page_size=50).endswith(
        "data_itemId=4110,pageIndex=3,pageSize=50.json"
    )


def test_build_detail_url() -> None:
    assert build_detail_url(1258731) == (
        "https://www.nfra.gov.cn/cn/static/data/DocInfo/"
        "SelectByDocId/data_docId=1258731.json"
    )


def test_build_list_html_url() -> None:
    url = build_list_html_url(4110)
    assert url.startswith("https://www.nfra.gov.cn/cn/view/pages/ItemList.html")
    assert "itemId=4110" in url


def test_parse_doc_ids_extracts_ids() -> None:
    body = (
        '{"rptCode":200,"msg":"成功","data":{"total":2,"rows":['
        '{"docId":1258731,"docTitle":"a"},{"docId":1259537,"docTitle":"b"}]}}'
    )
    assert parse_doc_ids(body) == [1258731, 1259537]


def test_parse_doc_ids_accepts_bytes() -> None:
    assert parse_doc_ids(b'{"rptCode":200,"data":{"rows":[{"docId":7}]}}') == [7]


def test_parse_doc_ids_empty_rows() -> None:
    assert parse_doc_ids('{"rptCode":200,"data":{"total":0,"rows":[]}}') == []


def test_parse_doc_ids_missing_rows() -> None:
    assert parse_doc_ids('{"rptCode":200,"data":{}}') == []


def test_parse_doc_ids_bad_code() -> None:
    assert parse_doc_ids('{"rptCode":404,"msg":"失败","data":{"rows":[{"docId":1}]}}') == []


def test_parse_doc_ids_invalid_json() -> None:
    assert parse_doc_ids("<html>404</html>") == []


def test_filter_pending_dedup_and_skip() -> None:
    doc_ids = [1, 2, 2, 3, 4]
    existing = {2, 4}
    assert filter_pending(doc_ids, existing) == [1, 3]


def test_filter_pending_empty() -> None:
    assert filter_pending([], {1, 2}) == []
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_nfra/test_parse.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'web_scraper_service.crawlers.nfra'`）

- [ ] **Step 4: 写最小实现 src/web_scraper_service/crawlers/nfra.py（仅纯函数部分）**

```python
"""nfra.gov.cn 文档快照采集：纯逻辑 + 异步编排。

纯逻辑（build_*/parse_doc_ids/filter_pending）可单测；异步编排
(discover_doc_ids / fetch_snapshots / run_crawl) 涉及网络与浏览器，
靠手动 smoke 验收。
"""

from __future__ import annotations

import json
from typing import Any

BASE = "https://www.nfra.gov.cn"


def build_list_url(item_id: int, page: int, page_size: int = 18) -> str:
    return (
        f"{BASE}/cn/static/data/DocInfo/SelectDocByItemIdAndChild/"
        f"data_itemId={item_id},pageIndex={page},pageSize={page_size}.json"
    )


def build_detail_url(doc_id: int) -> str:
    return f"{BASE}/cn/static/data/DocInfo/SelectByDocId/data_docId={doc_id}.json"


def build_list_html_url(item_id: int) -> str:
    return (
        f"{BASE}/cn/view/pages/ItemList.html?itemPId=923&itemId={item_id}"
        f"&itemUrl=ItemListRightList.html&itemName=zhujiguan"
    )


def parse_doc_ids(body: str | bytes) -> list[int]:
    """解析列表接口响应，返回 docId 列表。rptCode!=200、无 rows、非法 JSON 一律返回 []。"""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:
            return []
    try:
        payload: dict[str, Any] = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    if payload.get("rptCode") != 200:
        return []
    rows = (payload.get("data") or {}).get("rows") or []
    ids: list[int] = []
    for row in rows:
        doc_id = row.get("docId")
        if isinstance(doc_id, int):
            ids.append(doc_id)
    return ids


def filter_pending(doc_ids: list[int], existing: set[int]) -> list[int]:
    """去列表内重复 + 去已存在 doc_id，保持首次出现顺序。"""
    seen: set[int] = set()
    out: list[int] = []
    for doc_id in doc_ids:
        if doc_id in existing or doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
    return out
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_nfra/test_parse.py -v`
Expected: 全部 PASS（11 项）

- [ ] **Step 6: lint + 类型检查**

Run: `.venv/bin/ruff check src/web_scraper_service/crawlers/ tests/test_nfra/ && .venv/bin/python -m mypy src/web_scraper_service/crawlers/nfra.py`
Expected: 无 error

- [ ] **Step 7: Commit**

```bash
git add src/web_scraper_service/crawlers/ tests/test_nfra/
git commit -m "feat: add nfra crawler pure logic with tests"
```

---

### Task 4: 异步编排（列表浏览器 / 详情 HTTP / 写入）

**Files:**
- Modify: `src/web_scraper_service/crawlers/nfra.py`（在纯函数后追加编排函数）

**Interfaces:**
- Consumes: Task 2 的 `SnapshotRepo`/`init_table`/`SnapshotSession`；Task 3 的 `build_*`/`parse_doc_ids`/`filter_pending`；Scrapling `AsyncStealthySession`、`AsyncFetcher`
- Produces:
  - `async discover_doc_ids(session, item_id, pages) -> list[int]`
  - `async fetch_snapshots(doc_ids, concurrency, download_delay) -> list[dict]`（每项 `{"doc_id", "snapshot"}`）
  - `async run_crawl(item_id, pages, concurrency, download_delay) -> dict`（返回统计）

- [ ] **Step 1: 在 crawlers/nfra.py 顶部补充 import**

在文件 `from typing import Any` 之后、`BASE =` 之前补充：

```python
import asyncio

from loguru import logger

from web_scraper_service.storage.snapshot import (
    SnapshotRepo,
    SnapshotSession,
    init_table,
)
```

- [ ] **Step 2: 在文件末尾追加编排函数**

```python
# ── 异步编排（网络/浏览器，靠手动 smoke 验收）──────────────

_LIST_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE}/cn/view/pages/ItemList.html",
}

_DETAIL_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
}


async def discover_doc_ids(
    session: Any,
    item_id: int,
    pages: int,
) -> list[int]:
    """用浏览器持久会话遍历列表 API，收集 docId。

    先 fetch HTML 列表页触发 JS 生成会话 cookie，再逐页 fetch 列表 API。
    某页解析为空或失败则停止翻页。
    """
    doc_ids: list[int] = []
    # 1) 触发 cookie
    await session.fetch(build_list_html_url(item_id))
    # 2) 逐页采集
    for page in range(1, pages + 1):
        url = build_list_url(item_id, page)
        try:
            resp = await session.fetch(url, extra_headers=_LIST_HEADERS)
            ids = parse_doc_ids(resp.body)
        except Exception as exc:
            logger.warning("列表第 {} 页抓取失败: {}", page, exc)
            break
        if not ids:
            logger.info("列表第 {} 页无数据，停止翻页", page)
            break
        doc_ids.extend(ids)
        logger.info("列表第 {} 页获得 {} 条，累计 {}", page, len(ids), len(doc_ids))
        await asyncio.sleep(0.5)
    return doc_ids


async def _fetch_one(doc_id: int, download_delay: float) -> dict[str, Any] | None:
    """抓单个详情，返回 {'doc_id','snapshot'} 或 None。"""
    from scrapling.fetchers import AsyncFetcher

    try:
        resp = await AsyncFetcher.get(
            build_detail_url(doc_id),
            stealthy_headers=True,
            timeout=30,
            extra_headers=_DETAIL_HEADERS,
        )
        body = resp.body
        snapshot = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        return {"doc_id": doc_id, "snapshot": snapshot}
    except Exception as exc:
        logger.error("详情 doc_id={} 抓取失败: {}", doc_id, exc)
        return None
    finally:
        if download_delay > 0:
            await asyncio.sleep(download_delay)


async def fetch_snapshots(
    doc_ids: list[int],
    concurrency: int = 5,
    download_delay: float = 0.5,
) -> list[dict[str, Any]]:
    """并发抓取详情快照。"""
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(doc_id: int) -> dict[str, Any] | None:
        async with sem:
            return await _fetch_one(doc_id, download_delay)

    results = await asyncio.gather(*(_guarded(d) for d in doc_ids))
    return [r for r in results if r is not None]


async def run_crawl(
    item_id: int = 4110,
    pages: int = 5,
    concurrency: int = 5,
    download_delay: float = 0.5,
) -> dict[str, Any]:
    """完整编排：列表发现 → 过滤 → 详情抓取 → 写入。"""
    from scrapling.fetchers import AsyncStealthySession

    await init_table()

    # 阶段 1：列表发现
    async with AsyncStealthySession(headless=True) as session:
        doc_ids = await discover_doc_ids(session, item_id, pages)
    logger.info("共发现 {} 个 docId", len(doc_ids))
    if not doc_ids:
        return {"discovered": 0, "pending": 0, "stored": 0}

    # 阶段 2：过滤已存在
    async with SnapshotSession() as db:
        repo = SnapshotRepo(db)
        existing = await repo.existing_doc_ids(set(doc_ids))
    pending = filter_pending(doc_ids, existing)
    logger.info("待抓取 {} 个（已存在 {} 个）", len(pending), len(existing))
    if not pending:
        return {"discovered": len(doc_ids), "pending": 0, "stored": 0}

    # 阶段 3：详情抓取
    snapshots = await fetch_snapshots(pending, concurrency, download_delay)
    logger.info("成功抓取 {} / {} 个详情", len(snapshots), len(pending))

    # 阶段 4：写入
    async with SnapshotSession() as db:
        repo = SnapshotRepo(db)
        stored = await repo.insert_many(snapshots)
    logger.info("写入 web_snapshot {} 行", stored)
    return {
        "discovered": len(doc_ids),
        "pending": len(pending),
        "stored": stored,
    }
```

- [ ] **Step 3: 验证可导入且签名正确**

Run: `.venv/bin/python -c "import inspect; from web_scraper_service.crawlers import nfra; print([n for n in ('discover_doc_ids','fetch_snapshots','run_crawl') if hasattr(nfra,n)]); print(inspect.signature(nfra.run_crawl))"`
Expected: `['discover_doc_ids', 'fetch_snapshots', 'run_crawl']` 与 `(item_id=4110, pages=5, concurrency=5, download_delay=0.5) -> dict[str, Any]`

- [ ] **Step 4: lint + 类型检查**

Run: `.venv/bin/ruff check src/web_scraper_service/crawlers/ && .venv/bin/python -m mypy src/web_scraper_service/crawlers/nfra.py`
Expected: 无 error（`Any` 类型的 session 形参可接受）

- [ ] **Step 5: 确认纯逻辑测试仍通过**

Run: `.venv/bin/python -m pytest tests/test_nfra/test_parse.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/web_scraper_service/crawlers/nfra.py
git commit -m "feat: add nfra async orchestration (list via browser, detail via http)"
```

---

### Task 5: CLI 入口 scripts/crawl_nfra.py + Makefile

**Files:**
- Create: `scripts/crawl_nfra.py`
- Modify: `Makefile`（新增 `crawl-nfra` target）

**Interfaces:**
- Consumes: Task 4 的 `run_crawl`

- [ ] **Step 1: 创建 scripts/crawl_nfra.py**

```python
"""nfra.gov.cn 文档快照采集 CLI 入口。

用法:
    python scripts/crawl_nfra.py --pages 5 --item-id 4110
    make crawl-nfra
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from web_scraper_service.crawlers.nfra import run_crawl
from web_scraper_service.core.logging import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 nfra.gov.cn 文档快照")
    parser.add_argument("--pages", type=int, default=5, help="采集最新页数（默认 5）")
    parser.add_argument("--item-id", type=int, default=4110, help="栏目 itemId（默认 4110）")
    parser.add_argument("--concurrency", type=int, default=5, help="详情并发数（默认 5）")
    parser.add_argument("--download-delay", type=float, default=0.5, help="详情请求间隔秒（默认 0.5）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging()
    logger.info(
        "启动 nfra 采集: itemId={} pages={} concurrency={}",
        args.item_id, args.pages, args.concurrency,
    )
    stats = asyncio.run(
        run_crawl(
            item_id=args.item_id,
            pages=args.pages,
            concurrency=args.concurrency,
            download_delay=args.download_delay,
        )
    )
    logger.info("采集完成: {}", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 验证 CLI 可解析帮助**

Run: `.venv/bin/python scripts/crawl_nfra.py --help`
Expected: 输出 usage 与参数说明，无导入错误

- [ ] **Step 3: 在 Makefile 新增 target**

在 `Makefile` 的 `seed:` target 之后追加：

```make

crawl-nfra:
	uv run python scripts/crawl_nfra.py --pages $(or ${NFRA_PAGES},5) --item-id $(or ${NFRA_ITEM_ID},4110)
```

- [ ] **Step 4: 验证 make target 语法**

Run: `make -n crawl-nfra`
Expected: 打印 `uv run python scripts/crawl_nfra.py --pages 5 --item-id 4110`（`-n` 干跑不执行）

- [ ] **Step 5: lint + 类型检查**

Run: `.venv/bin/ruff check scripts/crawl_nfra.py && .venv/bin/python -m mypy scripts/crawl_nfra.py`
Expected: 无 error

- [ ] **Step 6: Commit**

```bash
git add scripts/crawl_nfra.py Makefile
git commit -m "feat: add crawl-nfra CLI and make target"
```

---

### Task 6: 手动 smoke 验收

**Files:** 无（仅运行验证）

**前置：** 已运行 `scrapling install`（浏览器就绪）；本地 PostgreSQL 可达且 `zbd_crawler_data` 库存在（或由 `init_table` 连接时手动创建 database）。

- [ ] **Step 1: 确认浏览器已安装**

Run: `.venv/bin/scrapling install --force` 或 `make install` 后确认无报错
Expected: 浏览器依赖就绪（如已装可跳过）

- [ ] **Step 2: 确保 zbd_crawler_data 库存在**

Run: `psql -h localhost -U scraper -d postgres -c "CREATE DATABASE zbd_crawler_data;" 2>/dev/null || true`
Expected: 库已存在或创建成功（若提示已存在则忽略）

- [ ] **Step 3: 小规模实跑（1 页）**

Run: `.venv/bin/python scripts/crawl_nfra.py --pages 1`
Expected: 日志依次出现"列表第 1 页获得 18 条"→"待抓取 18 个"→"成功抓取 N 个详情"→"写入 web_snapshot N 行"；进程退出码 0

- [ ] **Step 4: 验证数据落库**

Run: `psql -h localhost -U scraper -d zbd_crawler_data -c "SELECT doc_id, length(snapshot) AS snap_len, crawl_time FROM web_snapshot ORDER BY crawl_time DESC LIMIT 5;"`
Expected: 返回若干行，`snap_len > 0`，`snapshot` 为详情 JSON

- [ ] **Step 5: 验证 skip 语义（重复运行不重复写）**

Run: `.venv/bin/python scripts/crawl_nfra.py --pages 1`
Expected: 日志"待抓取 0 个"（全部已存在被过滤），`stored=0`

- [ ] **Step 6: 跑全量测试套件确认无回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全部 PASS（含 test_nfra/test_parse.py）

- [ ] **Step 7: 最终提交（如有 smoke 过程中的小修）**

```bash
git add -A
git commit -m "test: verify nfra crawler smoke run" || echo "nothing to commit"
```

---

## Self-Review 记录

- **Spec 覆盖**：列表/详情两阶段(Task 4)✓；跳过已存在(Task 2 repo + Task 3 filter)✓；独立库 web_snapshot(Task 2)✓；snapshot 存原始响应(Task 4 `_fetch_one` 用 `resp.body`)✓；默认 5 页(Task 4/5 默认参数)✓；CLI + make(Task 5)✓；单测(Task 3)✓；smoke 验收(Task 6)✓。
- **API 已验证**：`AsyncFetcher.get` 返回 `Awaitable[Response]`（项目 `fetchers/http.py` 已用同样方式 `await AsyncFetcher.get(...)`）；`Response.body` 为 bytes，`.decode` 存为 snapshot；`AsyncStealthySession` 为 async 上下文管理器，`await session.fetch(url, extra_headers=...)` 返回 Response；`FetcherSession` 在 0.4.9 无 `.get`，故详情阶段改用 `AsyncFetcher`（更简单且满足需求，无需持久 session）。
- **类型一致**：`run_crawl` 签名在 Task 4/5 一致；`SnapshotRepo.existing_doc_ids(set[int])->set[int]`、`insert_many(list[dict])->int` 在 Task 2/4 调用一致；`parse_doc_ids(str|bytes)->list[int]`、`filter_pending(list,set)->list` 在 Task 3/4 一致。
- **无占位符**：所有步骤含完整代码与确切命令。
