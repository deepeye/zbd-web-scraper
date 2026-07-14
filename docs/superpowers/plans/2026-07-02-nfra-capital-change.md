# nfra 注册资本/开业数据抽取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增与现有 `djg_data` 并行的 nfra 注册资本/总公司开业抽取链路，写入 `zbd_crawler_data.capital_change_data` 并提供独立 API。

**Architecture:** 复用现有 nfra 列表发现和动态详情抓取，新增资本变更专用存储、LLM extractor、采集编排、Celery task 和 API。现有任职资格采集入口、表结构和调度保持不变。

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, PostgreSQL, Celery, Scrapling `AsyncStealthySession`/`AsyncDynamicSession`, 百炼 OpenAI-compatible API, pytest.

## Global Constraints

- 新表命名为 `capital_change_data`，位于 `zbd_crawler_data` 库。
- 唯一约束为 `(doc_id, institution_name, change_type)`。
- 列表来源仍为 `item_id=4110` 和 `item_id=4291`。
- 标题过滤只保留包含「注册资本」或「开业」的文章。
- 「开业」文章只抽取总公司开业数据，不抽取分支机构、分公司、支公司、营业部等分支开业数据。
- 使用渲染后的 DOM 作为详情抽取输入。
- 金额和单位保留原文表达，不做数值归一化。
- 不合并到现有 `djg_data`。
- 不改变现有任职资格采集 API、表结构或调度。
- 不抽取股权变更 sheet。
- 不升级 APScheduler 到 4.x。
- Celery task 内保持 subprocess 模式，避免 worker 内事件循环和异步 engine 绑定问题。

---

## File Structure

- Create `src/web_scraper_service/storage/capital_change_data.py`: ORM model `CapitalChangeData`、repo `CapitalChangeDataRepo`、建表函数 `init_capital_change_table()`。
- Modify `src/web_scraper_service/api/deps.py`: 增加 `get_capital_change_data_repo()` 和 `CapitalChangeDataRepoD`。
- Modify `src/web_scraper_service/main.py`: startup 调用 `init_capital_change_table()`。
- Create `src/web_scraper_service/crawlers/nfra_capital_extractor.py`: 资本/开业标题过滤、详情字段解析、LLM prompt、LLM rows 解析与合并。
- Create `src/web_scraper_service/crawlers/nfra_capital.py`: 复用 `discover_doc_rows()`、`build_detail_html_url()`，编排两类 item_id 的采集。
- Create `scripts/crawl_nfra_capital.py`: Celery subprocess 可调用的 CLI。
- Modify `src/web_scraper_service/scheduler/engine.py`: 增加 `nfra_capital_crawl_task`。
- Modify `src/web_scraper_service/api/v1/nfra.py`: 增加 `/capital/crawl`、`/capital/crawl/{job_id}`、`/capital/data`。
- Create `tests/test_nfra/test_capital_change_storage.py`: 存储模型和建表自愈测试。
- Create `tests/test_nfra/test_capital_extractor.py`: 标题过滤、开业分支过滤、LLM JSON 解析和字段合并测试。
- Create `tests/test_nfra/test_capital_crawler.py`: 编排层复用列表发现、跳过已存在 doc、边抽边写统计测试。
- Modify `tests/test_api/test_nfra.py`: 增加资本/开业 API 测试。

---

### Task 1: Capital Change Storage

**Files:**
- Create: `src/web_scraper_service/storage/capital_change_data.py`
- Modify: `src/web_scraper_service/api/deps.py`
- Modify: `src/web_scraper_service/main.py`
- Test: `tests/test_nfra/test_capital_change_storage.py`

**Interfaces:**
- Consumes: `SnapshotSession` and `snapshot_engine` from `src/web_scraper_service/storage/snapshot.py`.
- Produces: `CapitalChangeData`, `CapitalChangeDataRepo`, `init_capital_change_table()`, `get_capital_change_data_repo(session)`, `CapitalChangeDataRepoD`.

- [ ] **Step 1: Write the failing storage tests**

Create `tests/test_nfra/test_capital_change_storage.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Date

from web_scraper_service.storage.capital_change_data import (
    CapitalChangeData,
    init_capital_change_table,
)


def test_capital_change_table_columns_and_unique_constraint() -> None:
    table = CapitalChangeData.__table__
    assert table.name == "capital_change_data"
    assert isinstance(table.c.publish_date.type, Date)
    assert table.c.publish_date.nullable is True
    assert {"doc_id", "institution_name", "change_type"}.issubset(table.c.keys())
    constraints = {constraint.name for constraint in table.constraints}
    assert "uq_capital_change_doc_institution_type" in constraints


@pytest.mark.asyncio
async def test_init_capital_change_table_creates_table() -> None:
    conn = MagicMock()
    conn.run_sync = AsyncMock()
    begin_context = AsyncMock()
    begin_context.__aenter__.return_value = conn
    begin_context.__aexit__.return_value = None

    mock_engine = MagicMock()
    mock_engine.begin.return_value = begin_context

    with patch("web_scraper_service.storage.capital_change_data.snapshot_engine", mock_engine):
        await init_capital_change_table()

    conn.run_sync.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_nfra/test_capital_change_storage.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'web_scraper_service.storage.capital_change_data'`.

- [ ] **Step 3: Implement storage model and repo**

Create `src/web_scraper_service/storage/capital_change_data.py`:

```python
"""zbd_crawler_data.capital_change_data storage."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Text, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from web_scraper_service.storage.snapshot import snapshot_engine


class _CapitalChangeBase(DeclarativeBase):
    pass


class CapitalChangeData(_CapitalChangeBase):
    __tablename__ = "capital_change_data"
    __table_args__ = (
        UniqueConstraint(
            "doc_id",
            "institution_name",
            "change_type",
            name="uq_capital_change_doc_institution_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issue_date: Mapped[str] = mapped_column(Text, default="")
    issuing_authority: Mapped[str] = mapped_column(Text, default="")
    doc_number: Mapped[str] = mapped_column(Text, default="")
    change_type: Mapped[str] = mapped_column(Text, default="")
    institution_name: Mapped[str] = mapped_column(Text, default="")
    registered_capital_before: Mapped[str] = mapped_column(Text, default="")
    registered_capital_change_method: Mapped[str] = mapped_column(Text, default="")
    change_amount: Mapped[str] = mapped_column(Text, default="")
    registered_capital_after: Mapped[str] = mapped_column(Text, default="")
    doc_title: Mapped[str] = mapped_column(Text, default="")
    doc_url: Mapped[str] = mapped_column(Text, default="")
    crawl_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


async def init_capital_change_table() -> None:
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_CapitalChangeBase.metadata.create_all)


class CapitalChangeDataRepo:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def existing_doc_ids(self, doc_ids: set[int]) -> set[int]:
        if not doc_ids:
            return set()
        stmt = select(CapitalChangeData.doc_id).where(
            CapitalChangeData.doc_id.in_(doc_ids)
        ).distinct()
        result = await self.session.execute(stmt)
        return {row[0] for row in result.all()}

    async def list_by_crawl_time(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[CapitalChangeData]:
        stmt = select(CapitalChangeData)
        if start_date is not None:
            stmt = stmt.where(CapitalChangeData.crawl_time >= start_date)
        if end_date is not None:
            stmt = stmt.where(CapitalChangeData.crawl_time <= end_date)
        stmt = (
            stmt.order_by(CapitalChangeData.crawl_time.desc(), CapitalChangeData.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_crawl_time(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(CapitalChangeData)
        if start_date is not None:
            stmt = stmt.where(CapitalChangeData.crawl_time >= start_date)
        if end_date is not None:
            stmt = stmt.where(CapitalChangeData.crawl_time <= end_date)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def insert_many(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        stmt = pg_insert(CapitalChangeData).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_capital_change_doc_institution_type"
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return getattr(result, "rowcount", 0) or 0
```

- [ ] **Step 4: Wire dependencies and startup**

Modify `src/web_scraper_service/api/deps.py` imports and definitions:

```python
from web_scraper_service.storage.capital_change_data import CapitalChangeDataRepo
from web_scraper_service.storage.djg_data import DjgDataRepo
```

Add after `get_djg_data_repo()`:

```python
def get_capital_change_data_repo(session: SnapshotSessionD) -> CapitalChangeDataRepo:
    return CapitalChangeDataRepo(session)


CapitalChangeDataRepoD = Annotated[
    CapitalChangeDataRepo, Depends(get_capital_change_data_repo)
]
```

Modify `src/web_scraper_service/main.py` imports:

```python
from web_scraper_service.storage.capital_change_data import init_capital_change_table
from web_scraper_service.storage.djg_data import init_djg_table
```

Call it in lifespan after `await init_djg_table()`:

```python
    await init_djg_table()
    await init_capital_change_table()
```

- [ ] **Step 5: Run storage tests**

Run: `pytest tests/test_nfra/test_capital_change_storage.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/web_scraper_service/storage/capital_change_data.py src/web_scraper_service/api/deps.py src/web_scraper_service/main.py tests/test_nfra/test_capital_change_storage.py
git commit -m "feat(nfra): add capital change storage"
```

---

### Task 2: Capital Extractor

**Files:**
- Create: `src/web_scraper_service/crawlers/nfra_capital_extractor.py`
- Test: `tests/test_nfra/test_capital_extractor.py`

**Interfaces:**
- Consumes: `settings.dashscope_api_key`, `settings.bailian_base_url`, `settings.bailian_model` from `config.py`.
- Produces: `is_capital_candidate(title: str) -> bool`, `parse_llm_rows(content: str) -> list[dict[str, str]]`, `extract_rows_llm(doc_id: int, html: str, doc_url: str) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write extractor tests**

Create `tests/test_nfra/test_capital_extractor.py`:

```python
from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scraper_service.crawlers.nfra_capital_extractor import (
    build_user_prompt,
    extract_rows_llm,
    is_capital_candidate,
    parse_llm_rows,
)

CAPITAL_HTML = """
<html><head>
<meta name="ArticleTitle" content="江苏金融监管局关于南京银行股份有限公司变更注册资本的批复">
<meta name="PubDate" content="2025-11-20">
</head><body>
<div class="wenzhang-title">江苏金融监管局关于南京银行股份有限公司变更注册资本的批复</div>
<div ng-bind-html="data.documentNo">苏金复〔2025〕411号</div>
<div id="wenzhang-content">南京银行股份有限公司：同意你行注册资本由10,007,016,973元变更为12,363,567,245元。2025年11月20日</div>
</body></html>
"""

OPENING_HTML = """
<html><head>
<meta name="ArticleTitle" content="国家金融监督管理总局关于瑞众人寿保险有限责任公司及其分支机构开业的批复">
<meta name="PubDate" content="2023-07-28">
</head><body>
<div class="wenzhang-title">国家金融监督管理总局关于瑞众人寿保险有限责任公司及其分支机构开业的批复</div>
<div ng-bind-html="data.documentNo">金复〔2023〕88号</div>
<div id="wenzhang-content">瑞众人寿保险有限责任公司：同意瑞众人寿保险有限责任公司开业，注册资本565亿元。其分支机构同时开业。2023年7月28日</div>
</body></html>
"""


def test_is_capital_candidate() -> None:
    assert is_capital_candidate("江苏金融监管局关于南京银行股份有限公司变更注册资本的批复") is True
    assert is_capital_candidate("国家金融监督管理总局关于瑞众人寿保险有限责任公司开业的批复") is True
    assert is_capital_candidate("江苏金融监管局关于张伟任职资格的批复") is False


def test_build_user_prompt_contains_capital_fields() -> None:
    prompt = build_user_prompt("某标题", "某文号", "某正文")
    assert "registered_capital_before" in prompt
    assert "registered_capital_after" in prompt
    assert "只抽取总公司" in prompt


def test_parse_llm_rows_valid_capital_change() -> None:
    content = json.dumps({"rows": [{
        "issue_date": "2025年11月20日",
        "issuing_authority": "江苏监管局",
        "change_type": "变更注册资本",
        "institution_name": "南京银行股份有限公司",
        "registered_capital_before": "10,007,016,973元",
        "registered_capital_change_method": "可转债转股",
        "change_amount": "",
        "registered_capital_after": "12,363,567,245元",
    }]})
    rows = parse_llm_rows(content)
    assert rows == [{
        "issue_date": "2025年11月20日",
        "issuing_authority": "江苏监管局",
        "change_type": "变更注册资本",
        "institution_name": "南京银行股份有限公司",
        "registered_capital_before": "10,007,016,973元",
        "registered_capital_change_method": "可转债转股",
        "change_amount": "",
        "registered_capital_after": "12,363,567,245元",
    }]


def test_parse_llm_rows_filters_branch_opening() -> None:
    content = json.dumps({"rows": [
        {
            "issue_date": "2023年7月28日",
            "issuing_authority": "国家金融监督管理总局",
            "change_type": "机构成立",
            "institution_name": "瑞众人寿保险有限责任公司",
            "registered_capital_before": "",
            "registered_capital_change_method": "",
            "change_amount": "",
            "registered_capital_after": "565亿元",
        },
        {
            "issue_date": "2023年7月28日",
            "issuing_authority": "国家金融监督管理总局",
            "change_type": "机构成立",
            "institution_name": "瑞众人寿保险有限责任公司北京分公司",
            "registered_capital_before": "",
            "registered_capital_change_method": "",
            "change_amount": "",
            "registered_capital_after": "",
        },
    ]})
    rows = parse_llm_rows(content)
    assert len(rows) == 1
    assert rows[0]["institution_name"] == "瑞众人寿保险有限责任公司"


def test_parse_llm_rows_invalid_json() -> None:
    assert parse_llm_rows("not json") == []


@pytest.mark.asyncio
async def test_extract_rows_llm_merges_code_fields() -> None:
    llm_content = json.dumps({"rows": [{
        "issue_date": "2025年11月20日",
        "issuing_authority": "江苏监管局",
        "change_type": "变更注册资本",
        "institution_name": "南京银行股份有限公司",
        "registered_capital_before": "10,007,016,973元",
        "registered_capital_change_method": "可转债转股",
        "change_amount": "",
        "registered_capital_after": "12,363,567,245元",
    }]})
    fake_choice = MagicMock()
    fake_choice.message.content = llm_content
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]

    with patch("web_scraper_service.crawlers.nfra_capital_extractor.AsyncOpenAI") as mock_client:
        client_inst = MagicMock()
        client_inst.chat.completions.create = AsyncMock(return_value=fake_resp)
        mock_client.return_value = client_inst
        rows = await extract_rows_llm(1234814, CAPITAL_HTML, "https://www.nfra.gov.cn/x")

    assert len(rows) == 1
    row = rows[0]
    assert row["doc_id"] == 1234814
    assert row["publish_date"] == date(2025, 11, 20)
    assert row["doc_number"] == "苏金复〔2025〕411号"
    assert row["doc_title"].startswith("江苏金融监管局关于")
    assert row["institution_name"] == "南京银行股份有限公司"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_nfra/test_capital_extractor.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'web_scraper_service.crawlers.nfra_capital_extractor'`.

- [ ] **Step 3: Implement extractor module**

Create `src/web_scraper_service/crawlers/nfra_capital_extractor.py`:

```python
"""LLM extraction for nfra capital change and head-office opening approvals."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger
from openai import AsyncOpenAI

from web_scraper_service.config import settings
from web_scraper_service.crawlers.nfra_extractor import (
    clean_prose,
    doc_number,
    doc_title,
    issuing_authority,
    publish_date,
)

SYSTEM_PROMPT = """你是一个金融监管文件信息抽取助手，专门从「金融机构注册资本变更批复」和「总公司开业批复」正文中抽取结构化信息。
严格按规则抽取，只输出 JSON，不要任何解释或多余文字。"""

_USER_TEMPLATE = """任务：从下方批复正文中抽取注册资本变更或总公司开业信息。

批复标题：{title}
发文函号：{number}
批复正文：
{prose}

输出字段：
- issue_date：发文日期，取正文末尾日期，格式保留原文，如 2025年11月20日
- issuing_authority：发文监管机构，如 江苏监管局、国家金融监督管理总局
- change_type：只允许 变更注册资本 或 机构成立
- institution_name：机构名称，必须是被批复的总公司或法人机构全称
- registered_capital_before：变更前注册资本，原文没有则空串
- registered_capital_change_method：注册资本变更方式，如 可转债转股、增加注册资本，原文没有则空串
- change_amount：变更金额，原文没有则空串
- registered_capital_after：变更后注册资本；开业文章写总公司注册资本

规则：
1. 标题或正文属于注册资本变更批复时，change_type 写 变更注册资本。
2. 标题或正文属于总公司开业批复时，change_type 写 机构成立。
3. 开业文章只抽取总公司，不抽取分支机构、分公司、支公司、营业部。
4. 一篇文章如包含多个符合条件的机构，每个机构一行。
5. 如果文章只涉及股权、任职资格、分支机构开业或其他无关内容，返回 {"rows": []}。
6. 金额和单位保留原文表达，不做数值归一化。
7. 严格输出 JSON，schema：{"rows":[{"issue_date":"","issuing_authority":"","change_type":"","institution_name":"","registered_capital_before":"","registered_capital_change_method":"","change_amount":"","registered_capital_after":""}]}。
"""

_FIELDS = (
    "issue_date",
    "issuing_authority",
    "change_type",
    "institution_name",
    "registered_capital_before",
    "registered_capital_change_method",
    "change_amount",
    "registered_capital_after",
)
_BRANCH_WORDS = ("分公司", "支公司", "中心支公司", "营业部", "分行", "支行")


def is_capital_candidate(title: str) -> bool:
    return "注册资本" in title or "开业" in title


def build_user_prompt(title: str, number: str, prose: str) -> str:
    return _USER_TEMPLATE.format(title=title, number=number, prose=prose)


def _is_branch_opening(row: dict[str, str]) -> bool:
    if row.get("change_type") != "机构成立":
        return False
    institution = row.get("institution_name", "")
    return any(word in institution for word in _BRANCH_WORDS)


def parse_llm_rows(content: str) -> list[dict[str, str]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    parsed: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = {field: str(row.get(field) or "").strip() for field in _FIELDS}
        if normalized["change_type"] not in {"变更注册资本", "机构成立"}:
            continue
        if not normalized["institution_name"]:
            continue
        if _is_branch_opening(normalized):
            continue
        parsed.append(normalized)
    return parsed


def _llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.dashscope_api_key, base_url=settings.bailian_base_url)


async def _call_llm(title: str, number: str, prose: str) -> str:
    client = _llm_client()
    resp = await client.chat.completions.create(
        model=settings.bailian_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(title, number, prose)},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


async def extract_rows_llm(doc_id: int, html: str, doc_url: str) -> list[dict[str, Any]]:
    title = doc_title(html)
    number = doc_number(html)
    prose = clean_prose(html)
    code_authority = issuing_authority(title)
    code_fields = {
        "doc_id": doc_id,
        "publish_date": publish_date(html),
        "doc_number": number,
        "doc_title": title,
        "doc_url": doc_url,
    }
    try:
        content = await _call_llm(title, number, prose)
    except Exception as exc:
        logger.error("资本变更 LLM 抽取失败 doc_id={}: {}", doc_id, exc)
        return []
    llm_rows = parse_llm_rows(content)
    return [
        {
            **code_fields,
            **row,
            "issuing_authority": row["issuing_authority"] or code_authority,
        }
        for row in llm_rows
    ]
```

- [ ] **Step 4: Run extractor tests**

Run: `pytest tests/test_nfra/test_capital_extractor.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/web_scraper_service/crawlers/nfra_capital_extractor.py tests/test_nfra/test_capital_extractor.py
git commit -m "feat(nfra): add capital change extractor"
```

---

### Task 3: Capital Crawl Orchestration and CLI

**Files:**
- Create: `src/web_scraper_service/crawlers/nfra_capital.py`
- Create: `scripts/crawl_nfra_capital.py`
- Test: `tests/test_nfra/test_capital_crawler.py`

**Interfaces:**
- Consumes: `discover_doc_rows(session, item_id, pages)`, `build_detail_html_url(doc_id)`, `CapitalChangeDataRepo`, `SnapshotSession`, `extract_rows_llm()`.
- Produces: `run_crawl(item_id: int | None = None, pages: int = 5, concurrency: int = 2, download_delay: float = 1.0) -> dict[str, Any]`.

- [ ] **Step 1: Write crawler orchestration tests**

Create `tests/test_nfra/test_capital_crawler.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scraper_service.crawlers import nfra_capital


@pytest.mark.asyncio
async def test_run_crawl_uses_default_item_ids_and_title_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    rows_by_item = {
        4110: [
            {"docId": 1, "docTitle": "关于A公司变更注册资本的批复"},
            {"docId": 2, "docTitle": "关于张伟任职资格的批复"},
        ],
        4291: [
            {"docId": 3, "docTitle": "关于B公司开业的批复"},
        ],
    }

    async def fake_discover(session, item_id, pages):
        return rows_by_item[item_id]

    class FakeRepo:
        def __init__(self, session):
            pass

        async def existing_doc_ids(self, doc_ids):
            return set()

        async def insert_many(self, rows):
            return len(rows)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeBrowserSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def fetch(self, *args, **kwargs):
            resp = MagicMock()
            resp.html_content = "<html></html>"
            return resp

    monkeypatch.setattr(nfra_capital, "discover_doc_rows", fake_discover)
    monkeypatch.setattr(nfra_capital, "SnapshotSession", FakeSession)
    monkeypatch.setattr(nfra_capital, "CapitalChangeDataRepo", FakeRepo)
    monkeypatch.setattr(nfra_capital, "init_capital_change_table", AsyncMock())
    monkeypatch.setattr(nfra_capital, "extract_rows_llm", AsyncMock(return_value=[{"doc_id": 1}]))

    with patch.dict("sys.modules", {"scrapling.fetchers": MagicMock(AsyncDynamicSession=FakeBrowserSession, AsyncStealthySession=FakeBrowserSession)}):
        stats = await nfra_capital.run_crawl(pages=1, download_delay=0)

    assert stats == {"discovered": 3, "qualified": 2, "pending": 2, "extracted_rows": 2, "stored": 2}


@pytest.mark.asyncio
async def test_run_crawl_skips_existing_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_discover(session, item_id, pages):
        return [{"docId": 1, "docTitle": "关于A公司变更注册资本的批复"}]

    class FakeRepo:
        def __init__(self, session):
            pass

        async def existing_doc_ids(self, doc_ids):
            return {1}

        async def insert_many(self, rows):
            return len(rows)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeBrowserSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(nfra_capital, "discover_doc_rows", fake_discover)
    monkeypatch.setattr(nfra_capital, "SnapshotSession", FakeSession)
    monkeypatch.setattr(nfra_capital, "CapitalChangeDataRepo", FakeRepo)
    monkeypatch.setattr(nfra_capital, "init_capital_change_table", AsyncMock())
    extract = AsyncMock(return_value=[{"doc_id": 1}])
    monkeypatch.setattr(nfra_capital, "extract_rows_llm", extract)

    with patch.dict("sys.modules", {"scrapling.fetchers": MagicMock(AsyncDynamicSession=FakeBrowserSession, AsyncStealthySession=FakeBrowserSession)}):
        stats = await nfra_capital.run_crawl(item_id=4110, pages=1, download_delay=0)

    assert stats == {"discovered": 1, "qualified": 1, "pending": 0, "extracted_rows": 0, "stored": 0}
    extract.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_nfra/test_capital_crawler.py -v`

Expected: FAIL with `ImportError` for `web_scraper_service.crawlers.nfra_capital`.

- [ ] **Step 3: Implement crawler orchestration**

Create `src/web_scraper_service/crawlers/nfra_capital.py`:

```python
"""nfra capital change/head-office opening crawler orchestration."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from web_scraper_service.crawlers.nfra import (
    build_detail_html_url,
    discover_doc_rows,
)
from web_scraper_service.crawlers.nfra_capital_extractor import (
    extract_rows_llm,
    is_capital_candidate,
)
from web_scraper_service.storage.capital_change_data import (
    CapitalChangeDataRepo,
    init_capital_change_table,
)
from web_scraper_service.storage.snapshot import SnapshotSession

DEFAULT_ITEM_IDS = (4110, 4291)


async def _fetch_detail_rows(
    session: Any,
    doc_id: int,
    doc_url: str,
    download_delay: float,
) -> list[dict[str, Any]]:
    try:
        resp = await session.fetch(doc_url, network_idle=True, timeout=60000)
        html = resp.html_content or ""
        return await extract_rows_llm(doc_id, html, doc_url)
    except Exception as exc:
        logger.error("资本变更详情 doc_id={} 抽取失败: {}", doc_id, exc)
        return []
    finally:
        if download_delay > 0:
            await asyncio.sleep(download_delay)


async def run_crawl(
    item_id: int | None = None,
    pages: int = 5,
    concurrency: int = 2,
    download_delay: float = 1.0,
) -> dict[str, Any]:
    from scrapling.fetchers import AsyncDynamicSession, AsyncStealthySession

    await init_capital_change_table()

    item_ids = (item_id,) if item_id is not None else DEFAULT_ITEM_IDS
    rows: list[dict[str, Any]] = []
    async with AsyncStealthySession(headless=True) as session:
        for current_item_id in item_ids:
            rows.extend(await discover_doc_rows(session, current_item_id, pages))

    if not rows:
        return {"discovered": 0, "qualified": 0, "pending": 0, "extracted_rows": 0, "stored": 0}

    qualified = [row for row in rows if is_capital_candidate(row.get("docTitle", ""))]
    if not qualified:
        return {"discovered": len(rows), "qualified": 0, "pending": 0, "extracted_rows": 0, "stored": 0}

    pending_ids = {int(row["docId"]) for row in qualified}
    async with SnapshotSession() as db:
        repo = CapitalChangeDataRepo(db)
        existing = await repo.existing_doc_ids(pending_ids)
    pending = [row for row in qualified if int(row["docId"]) not in existing]
    if not pending:
        return {"discovered": len(rows), "qualified": len(qualified), "pending": 0, "extracted_rows": 0, "stored": 0}

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(row: dict[str, Any]) -> tuple[int, int]:
        doc_id = int(row["docId"])
        async with sem:
            batch = await _fetch_detail_rows(
                session,
                doc_id,
                build_detail_html_url(doc_id),
                download_delay,
            )
        if not batch:
            return 0, 0
        async with SnapshotSession() as db:
            repo = CapitalChangeDataRepo(db)
            stored = await repo.insert_many(batch)
        logger.info("capital doc_id={} 抽取 {} 行，写入 {} 行", doc_id, len(batch), stored)
        return len(batch), stored

    async with AsyncDynamicSession(headless=True) as session:
        results = await asyncio.gather(*(_guarded(row) for row in pending))

    extracted_rows = sum(result[0] for result in results)
    stored = sum(result[1] for result in results)
    return {
        "discovered": len(rows),
        "qualified": len(qualified),
        "pending": len(pending),
        "extracted_rows": extracted_rows,
        "stored": stored,
    }
```

- [ ] **Step 4: Add CLI entry**

Create `scripts/crawl_nfra_capital.py`:

```python
"""nfra.gov.cn 注册资本/开业数据采集 CLI 入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from loguru import logger

from web_scraper_service.core.logging import setup_logging
from web_scraper_service.crawlers.nfra_capital import run_crawl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 nfra.gov.cn 注册资本/开业批复数据")
    parser.add_argument("--pages", type=int, default=5, help="采集最新页数（默认 5）")
    parser.add_argument("--item-id", type=int, default=None, help="栏目 itemId；不传则采集 4110 和 4291")
    parser.add_argument("--concurrency", type=int, default=2, help="详情并发数（默认 2，浏览器+LLM）")
    parser.add_argument("--download-delay", type=float, default=1.0, help="详情请求间隔秒（默认 1.0）")
    parser.add_argument(
        "--json-out",
        action="store_true",
        help="采集完成后向 stdout 打印单行 JSON 统计（供 Celery 子进程任务解析）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging()
    logger.info(
        "启动 nfra 注册资本/开业采集: itemId={} pages={} concurrency={}",
        args.item_id,
        args.pages,
        args.concurrency,
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
    if args.json_out:
        print(json.dumps(stats, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run crawler tests**

Run: `pytest tests/test_nfra/test_capital_crawler.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/web_scraper_service/crawlers/nfra_capital.py scripts/crawl_nfra_capital.py tests/test_nfra/test_capital_crawler.py
git commit -m "feat(nfra): add capital change crawler"
```

---

### Task 4: Celery Task and API

**Files:**
- Modify: `src/web_scraper_service/scheduler/engine.py`
- Modify: `src/web_scraper_service/api/v1/nfra.py`
- Modify: `tests/test_api/test_nfra.py`

**Interfaces:**
- Consumes: `scripts/crawl_nfra_capital.py --json-out`, `CapitalChangeDataRepoD`.
- Produces: `nfra_capital_crawl_task(self, item_id: int | None, pages: int) -> dict[str, Any]`, `POST /api/v1/nfra/capital/crawl`, `GET /api/v1/nfra/capital/crawl/{job_id}`, `GET /api/v1/nfra/capital/data`.

- [ ] **Step 1: Add API tests**

Append to `tests/test_api/test_nfra.py`:

```python
from web_scraper_service.api.deps import get_capital_change_data_repo
from web_scraper_service.storage.capital_change_data import CapitalChangeData


def _fake_capital_row(
    *,
    id: int = 1,
    doc_id: int = 1234814,
    publish_date: date | None = date(2025, 11, 20),
    issue_date: str = "2025年11月20日",
    issuing_authority: str = "江苏监管局",
    doc_number: str = "苏金复〔2025〕411号",
    change_type: str = "变更注册资本",
    institution_name: str = "南京银行股份有限公司",
    registered_capital_before: str = "10,007,016,973元",
    registered_capital_change_method: str = "可转债转股",
    change_amount: str = "",
    registered_capital_after: str = "12,363,567,245元",
    doc_title: str = "江苏金融监管局关于南京银行股份有限公司变更注册资本的批复",
    doc_url: str = "https://www.nfra.gov.cn/x",
    crawl_time: datetime | None = None,
) -> CapitalChangeData:
    row = CapitalChangeData()
    row.id = id
    row.doc_id = doc_id
    row.publish_date = publish_date
    row.issue_date = issue_date
    row.issuing_authority = issuing_authority
    row.doc_number = doc_number
    row.change_type = change_type
    row.institution_name = institution_name
    row.registered_capital_before = registered_capital_before
    row.registered_capital_change_method = registered_capital_change_method
    row.change_amount = change_amount
    row.registered_capital_after = registered_capital_after
    row.doc_title = doc_title
    row.doc_url = doc_url
    row.crawl_time = crawl_time or datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    return row


def test_post_capital_crawl_defaults(client: TestClient, _api_key: str) -> None:
    fake = MagicMock()
    fake.id = "capital-job-123"
    with patch("web_scraper_service.api.v1.nfra.nfra_capital_crawl_task") as task:
        task.apply_async.return_value = fake
        resp = client.post(
            "/api/v1/nfra/capital/crawl",
            json={},
            headers={"X-API-Key": _api_key},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["job_id"] == "capital-job-123"
    assert data["item_id"] is None
    assert data["pages"] == 5
    assert data["status"] == "pending"
    _, kwargs = task.apply_async.call_args
    assert kwargs["args"] == [None, 5]


def test_post_capital_crawl_custom_item_id(client: TestClient, _api_key: str) -> None:
    fake = MagicMock()
    fake.id = "capital-job-456"
    with patch("web_scraper_service.api.v1.nfra.nfra_capital_crawl_task") as task:
        task.apply_async.return_value = fake
        resp = client.post(
            "/api/v1/nfra/capital/crawl",
            json={"item_id": 4291, "pages": 3},
            headers={"X-API-Key": _api_key},
        )
    assert resp.status_code == 200
    _, kwargs = task.apply_async.call_args
    assert kwargs["args"] == [4291, 3]


def test_post_capital_crawl_invalid_pages(client: TestClient, _api_key: str) -> None:
    resp = client.post(
        "/api/v1/nfra/capital/crawl",
        json={"pages": 0},
        headers={"X-API-Key": _api_key},
    )
    assert resp.status_code == 400


def test_get_capital_status_success(client: TestClient, _api_key: str) -> None:
    with patch("web_scraper_service.api.v1.nfra.AsyncResult") as ar:
        inst = MagicMock()
        inst.state = "SUCCESS"
        inst.result = {"discovered": 3, "qualified": 2, "pending": 2, "extracted_rows": 2, "stored": 2}
        ar.return_value = inst
        resp = client.get(
            "/api/v1/nfra/capital/crawl/capital-job-1",
            headers={"X-API-Key": _api_key},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["job_id"] == "capital-job-1"
    assert data["status"] == "success"
    assert data["result"]["stored"] == 2


def test_get_capital_data(client: TestClient, _api_key: str) -> None:
    repo = MagicMock()
    repo.list_by_crawl_time = AsyncMock(return_value=[_fake_capital_row()])
    repo.count_by_crawl_time = AsyncMock(return_value=1)
    client.app.dependency_overrides[get_capital_change_data_repo] = lambda: repo
    try:
        resp = client.get(
            "/api/v1/nfra/capital/data",
            params={"page": 1, "size": 20},
            headers={"X-API-Key": _api_key},
        )
    finally:
        client.app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    row = body["data"][0]
    assert row["doc_id"] == 1234814
    assert row["publish_date"] == "2025-11-20"
    assert row["change_type"] == "变更注册资本"
    assert row["institution_name"] == "南京银行股份有限公司"
    assert row["registered_capital_after"] == "12,363,567,245元"
    assert body["pagination"]["total"] == 1
```

If the appended imports duplicate existing imports, merge them into the existing import block instead of leaving duplicate import lines.

- [ ] **Step 2: Run API tests to verify they fail**

Run: `pytest tests/test_api/test_nfra.py -v`

Expected: FAIL because `get_capital_change_data_repo`, `nfra_capital_crawl_task`, and `/api/v1/nfra/capital/*` are not wired yet.

- [ ] **Step 3: Add Celery task**

Modify `src/web_scraper_service/scheduler/engine.py` after `nfra_crawl_task`:

```python
@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def nfra_capital_crawl_task(self: Any, item_id: int | None, pages: int) -> dict[str, Any]:
    import json
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    cmd = [
        sys.executable,
        str(repo_root / "scripts" / "crawl_nfra_capital.py"),
        "--pages",
        str(pages),
        "--json-out",
    ]
    if item_id is not None:
        cmd.extend(["--item-id", str(item_id)])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=3600,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            raise RuntimeError(f"crawl_nfra_capital.py exited {proc.returncode}: {tail[-500:]}")
        stats: dict[str, Any] = {}
        for line in reversed(proc.stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    stats = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        logger.info("nfra capital crawl done: item_id={} pages={} stats={}", item_id, pages, stats)
        return stats
    except Exception as exc:
        logger.error("nfra capital crawl task failed: item_id={} pages={} err={}", item_id, pages, exc)
        raise self.retry(exc=exc)
```

- [ ] **Step 4: Add API request model and endpoints**

Modify `src/web_scraper_service/api/v1/nfra.py` imports:

```python
from web_scraper_service.api.deps import (
    ApiKey,
    CapitalChangeDataRepoD,
    DjgDataRepoD,
    Pagination,
)
from web_scraper_service.scheduler.engine import nfra_capital_crawl_task, nfra_crawl_task
```

Add request model after `CrawlRequest`:

```python
class CapitalCrawlRequest(BaseModel):
    item_id: int | None = Field(default=None)
    pages: int = Field(default=5)
```

Add endpoints after existing `/crawl/{job_id}` status endpoint and before `/data`:

```python
@router.post("/capital/crawl")
async def capital_crawl(body: CapitalCrawlRequest, _: ApiKey) -> dict[str, Any]:
    if body.pages < 1:
        raise HTTPException(status_code=400, detail="pages must be >= 1")
    if body.item_id is not None and body.item_id < 1:
        raise HTTPException(status_code=400, detail="item_id must be >= 1")
    job_id = str(uuid.uuid4())
    result = nfra_capital_crawl_task.apply_async(
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


@router.get("/capital/crawl/{job_id}")
async def capital_crawl_status(job_id: str, _: ApiKey) -> dict[str, Any]:
    result = AsyncResult(job_id)
    state = result.state.lower()
    payload: dict[str, Any] = {"job_id": job_id, "status": state, "result": None}
    if result.state == "SUCCESS":
        payload["status"] = "success"
        payload["result"] = result.result
    elif result.state == "FAILURE":
        payload["status"] = "failure"
        payload["result"] = str(result.result)
    return ok(payload)


@router.get("/capital/data")
async def list_capital_data(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    repo: CapitalChangeDataRepoD = None,  # type: ignore[assignment]
    _: ApiKey = None,  # type: ignore[assignment]
    pagination: Pagination = None,  # type: ignore[assignment]
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
                "id": str(row.id),
                "doc_id": row.doc_id,
                "publish_date": row.publish_date.isoformat() if row.publish_date else None,
                "issue_date": row.issue_date,
                "issuing_authority": row.issuing_authority,
                "doc_number": row.doc_number,
                "change_type": row.change_type,
                "institution_name": row.institution_name,
                "registered_capital_before": row.registered_capital_before,
                "registered_capital_change_method": row.registered_capital_change_method,
                "change_amount": row.change_amount,
                "registered_capital_after": row.registered_capital_after,
                "doc_title": row.doc_title,
                "doc_url": row.doc_url,
                "crawl_time": str(row.crawl_time),
            }
            for row in rows
        ],
        PaginationMeta(page=pagination.page, size=pagination.size, total=total),
    )
```

- [ ] **Step 5: Run API tests**

Run: `pytest tests/test_api/test_nfra.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/web_scraper_service/scheduler/engine.py src/web_scraper_service/api/v1/nfra.py tests/test_api/test_nfra.py
git commit -m "feat(nfra): add capital change API"
```

---

### Task 5: Full Verification and Docs

**Files:**
- Modify: `docs/API.md`
- Test: existing tests touched by Tasks 1-4

**Interfaces:**
- Consumes: all interfaces from Tasks 1-4.
- Produces: documented API contract for `/api/v1/nfra/capital/*`.

- [ ] **Step 1: Add API documentation section**

Modify `docs/API.md` in the nfra section by adding this table and example near the existing nfra data API documentation:

```markdown
### nfra 注册资本/开业采集

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/nfra/capital/crawl` | 手动触发注册资本/总公司开业采集；默认同时采集 `item_id=4110` 和 `4291` |
| GET | `/api/v1/nfra/capital/crawl/{job_id}` | 查询 Celery 任务状态 |
| GET | `/api/v1/nfra/capital/data` | 按 `crawl_time` 范围分页查询 `capital_change_data` |

`POST /api/v1/nfra/capital/crawl` 请求体：

```json
{
  "item_id": 4291,
  "pages": 3
}
```

`item_id` 可省略；省略时采集 `4110` 和 `4291`。

`GET /api/v1/nfra/capital/data` 响应行字段：

```json
{
  "id": "1",
  "doc_id": 1234814,
  "publish_date": "2025-11-20",
  "issue_date": "2025年11月20日",
  "issuing_authority": "江苏监管局",
  "doc_number": "苏金复〔2025〕411号",
  "change_type": "变更注册资本",
  "institution_name": "南京银行股份有限公司",
  "registered_capital_before": "10,007,016,973元",
  "registered_capital_change_method": "可转债转股",
  "change_amount": "",
  "registered_capital_after": "12,363,567,245元",
  "doc_title": "江苏金融监管局关于南京银行股份有限公司变更注册资本的批复",
  "doc_url": "https://www.nfra.gov.cn/...",
  "crawl_time": "2026-07-02 10:00:00+00:00"
}
```
```

- [ ] **Step 2: Run focused tests**

Run: `pytest tests/test_nfra/test_capital_change_storage.py tests/test_nfra/test_capital_extractor.py tests/test_nfra/test_capital_crawler.py tests/test_api/test_nfra.py -v`

Expected: PASS.

- [ ] **Step 3: Run lint on changed Python files**

Run: `ruff check src/web_scraper_service/storage/capital_change_data.py src/web_scraper_service/crawlers/nfra_capital_extractor.py src/web_scraper_service/crawlers/nfra_capital.py src/web_scraper_service/api/deps.py src/web_scraper_service/main.py src/web_scraper_service/scheduler/engine.py src/web_scraper_service/api/v1/nfra.py scripts/crawl_nfra_capital.py tests/test_nfra/test_capital_change_storage.py tests/test_nfra/test_capital_extractor.py tests/test_nfra/test_capital_crawler.py tests/test_api/test_nfra.py`

Expected: PASS.

- [ ] **Step 4: Run type checking if repo lint command includes mypy**

Run: `make lint`

Expected: PASS. If it fails on unrelated pre-existing files, do not fix unrelated files; record the failing file and command output in the handoff.

- [ ] **Step 5: Run manual CLI smoke test with one page when credentials and browser deps exist**

Run: `python scripts/crawl_nfra_capital.py --pages 1 --json-out`

Expected: final stdout line is JSON containing `discovered`, `qualified`, `pending`, `extracted_rows`, and `stored`.

If local `DASHSCOPE_API_KEY` or Scrapling browser dependencies are missing, skip this command and state the missing prerequisite in the handoff.

- [ ] **Step 6: Commit**

```bash
git add docs/API.md
git commit -m "docs(nfra): document capital change API"
```

---

## Self-Review

- Spec coverage: Tasks cover new table, unique key, title filtering, total-company opening filtering, rendered DOM extractor, LLM prompt, crawler orchestration, Celery subprocess task, independent API, docs, and tests.
- Placeholder scan: no `TBD`, `TODO`, `implement later`, or unspecified test instructions remain.
- Type consistency: `CapitalChangeDataRepo`, `init_capital_change_table`, `extract_rows_llm`, `run_crawl`, and `nfra_capital_crawl_task` names are consistent across tasks.
