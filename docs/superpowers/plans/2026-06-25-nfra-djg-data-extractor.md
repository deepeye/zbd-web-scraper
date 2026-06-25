# nfra djg_data 结构化抽取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修改 nfra 采集详情阶段：DynamicFetcher 打开 HTML 详情页，hybrid 抽取（meta 用选择器、人/职务/机构/日期用百炼 LLM），写入 `zbd_crawler_data.djg_data`，只处理标题含「任职资格」的文档，一人一行。

**Architecture:** 列表阶段复用浏览器发现，改为返回 `(docId, docTitle)` 并在列表阶段按标题含「任职资格」过滤；详情阶段用 `AsyncDynamicSession` 持久浏览器并发打开详情 HTML，`nfra_extractor.py` 代码侧取 doc_title/authority/doc_number/prose，调百炼 `qwen3.5-35b-a3b` 抽 person_name/position/institution_name/issue_date，合并写入 `djg_data`（ON CONFLICT (doc_id,person_name) DO NOTHING，跳过已存在 doc_id）。

**Tech Stack:** Python 3.12 · Scrapling 0.4.9（AsyncDynamicSession）· OpenAI SDK（百炼兼容）· SQLAlchemy 2.x async · loguru · pytest

## Global Constraints

- 百炼模型 id `qwen3.5-35b-a3b`（用户确认）；OpenAI 兼容 API，base_url `https://dashscope.aliyuncs.com/compatible-mode/v1`，api_key 来自环境变量 `DASHSCOPE_API_KEY`。
- 列表/详情/抽取全部用代码或 LLM，不写正则解析人名职务。
- `djg_data` 与 `web_snapshot` 同库 `zbd_crawler_data`，复用 `snapshot_engine`/`SnapshotSession`，不新建 engine。
- `djg_data.doc_id` 为 bigint，唯一约束 `(doc_id, person_name)`，写入 `ON CONFLICT (doc_id, person_name) DO NOTHING`。
- 列名英文 snake_case。表由 `init_djg_table()` 用 `CREATE TABLE IF NOT EXISTS` 创建，不纳入 Alembic。
- 跳过粒度：详情抓取前查 `djg_data` 已有 doc_id 集合，整 doc 跳过。
- web_snapshot 代码保留不动，`run_crawl` 不再写入 web_snapshot。
- mypy strict 全局开启；代码须过 mypy。venv 在 `.venv`，命令用 `.venv/bin/python`、`.venv/bin/ruff`、`.venv/bin/mypy`、`.venv/bin/pytest`。
- 两样本 HTML fixture 已存在：`tests/test_nfra/fixtures/doc_1258731_main.html`、`tests/test_nfra/fixtures/doc_1258343_jiangsu.html`。
- 约束引自 spec：`docs/superpowers/specs/2026-06-25-nfra-djg-data-extractor-design.md`。

## File Structure

| 文件 | 职责 |
|------|------|
| `pyproject.toml` | 加 `openai>=1.50` 依赖 |
| `src/web_scraper_service/config.py` | 加 dashscope_api_key / bailian_base_url / bailian_model |
| `.env.example` | 加 DASHSCOPE_API_KEY= / BAILIAN_MODEL= |
| `src/web_scraper_service/storage/djg_data.py` | DjgData 模型 + DjgDataRepo + init_djg_table |
| `src/web_scraper_service/crawlers/nfra_extractor.py` | 代码侧解析 + LLM 抽取 |
| `src/web_scraper_service/crawlers/nfra.py` | 详情阶段改 DynamicSession+LLM+写 djg_data；parse_doc_rows/discover_doc_rows/build_detail_html_url；标题过滤 |
| `scripts/crawl_nfra.py` | 默认参数（并发2/delay1.0）+ 日志 |
| `tests/test_nfra/test_extractor.py` | 代码侧解析 + LLM 合并（mock）单测 |

---

### Task 1: 依赖与配置（openai + 百炼配置）

**Files:**
- Modify: `pyproject.toml`（dependencies 加 openai）
- Modify: `src/web_scraper_service/config.py`（Snapshot DB 段后加 Bailian 段）
- Modify: `.env.example`（Snapshot Database 段后加 Bailian 段）

**Interfaces:**
- Produces: `settings.dashscope_api_key: str`、`settings.bailian_base_url: str`、`settings.bailian_model: str`（Task 4 抽取模块依赖）

- [ ] **Step 1: pyproject.toml 加 openai 依赖**

在 `pyproject.toml` 的 `dependencies` 列表（`"boto3"`/`"aioboto3"` 行附近）加一行：

```toml
    # LLM extraction (Bailian / Qwen, OpenAI-compatible)
    "openai>=1.50",
```

- [ ] **Step 2: config.py 加百炼配置**

在 `src/web_scraper_service/config.py` 的 `snapshot_database_url` 属性之后、`# ── S3 ──` 段之前插入：

```python
    # ── Bailian (Qwen LLM 抽取，OpenAI 兼容) ────────────────
    dashscope_api_key: str = ""
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    bailian_model: str = "qwen3.5-35b-a3b"
```

- [ ] **Step 3: .env.example 加百炼配置**

在 `.env.example` 的 `# ── Snapshot Database ──` 段之后、`# ── Scrapling / Fetcher ──` 段之前插入：

```
# ── Bailian (Qwen LLM 抽取) ───────────────────────────────
# OpenAI 兼容 API，用于 djg_data 字段抽取
DASHSCOPE_API_KEY=
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BAILIAN_MODEL=qwen3.5-35b-a3b
```

- [ ] **Step 4: 同步依赖并验证配置加载**

Run: `uv sync --quiet && .venv/bin/python -c "from web_scraper_service.config import settings; print(settings.bailian_base_url, settings.bailian_model)"`
Expected: 输出 `https://dashscope.aliyuncs.com/compatible-mode/v1 qwen3.5-35b-a3b`，且 `openai` 安装成功。

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/web_scraper_service/config.py .env.example
git commit -m "feat: add openai dep and bailian config for LLM extraction"
```

---

### Task 2: djg_data 存储层

**Files:**
- Create: `src/web_scraper_service/storage/djg_data.py`

**Interfaces:**
- Consumes: `snapshot_engine`、`SnapshotSession`（来自 `storage/snapshot.py`）、`settings`
- Produces:
  - `DjgData`（模型：id bigserial PK，doc_id bigint，issue_date/issuing_authority/doc_number/institution_name/person_name/position/doc_title/doc_url text，crawl_time timestamptz；唯一约束 `(doc_id, person_name)`）
  - `async init_djg_table() -> None`
  - `class DjgDataRepo`：`async existing_doc_ids(doc_ids: set[int]) -> set[int]`、`async insert_many(rows: list[dict]) -> int`

- [ ] **Step 1: 创建 storage/djg_data.py**

```python
"""djg_data 表存储：nfra 任职资格批复抽取结果（一人一行）。

与 web_snapshot 同库 zbd_crawler_data，复用 snapshot_engine，不走 Alembic；
表由 init_djg_table() 用 CREATE TABLE IF NOT EXISTS 创建。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Text, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from web_scraper_service.storage.snapshot import SnapshotSession, snapshot_engine


class _DjgBase(DeclarativeBase):
    pass


class DjgData(_DjgBase):
    __tablename__ = "djg_data"
    __table_args__ = (UniqueConstraint("doc_id", "person_name", name="uq_djg_doc_person"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    issue_date: Mapped[str] = mapped_column(Text, default="")
    issuing_authority: Mapped[str] = mapped_column(Text, default="")
    doc_number: Mapped[str] = mapped_column(Text, default="")
    institution_name: Mapped[str] = mapped_column(Text, default="")
    person_name: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[str] = mapped_column(Text, default="")
    doc_title: Mapped[str] = mapped_column(Text, default="")
    doc_url: Mapped[str] = mapped_column(Text, default="")
    crawl_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


async def init_djg_table() -> None:
    """CREATE TABLE IF NOT EXISTS djg_data."""
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_DjgBase.metadata.create_all)


class DjgDataRepo:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def existing_doc_ids(self, doc_ids: set[int]) -> set[int]:
        """返回 doc_ids 中已存在任一行于 djg_data 的 doc_id 集合。"""
        if not doc_ids:
            return set()
        stmt = select(DjgData.doc_id).where(DjgData.doc_id.in_(doc_ids)).distinct()
        result = await self.session.execute(stmt)
        return {row[0] for row in result.all()}

    async def insert_many(self, rows: list[dict[str, Any]]) -> int:
        """批量插入，已存在 (doc_id,person_name) 跳过。返回新增行数。"""
        if not rows:
            return 0
        stmt = pg_insert(DjgData).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_djg_doc_person"
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return getattr(result, "rowcount", 0) or 0
```

注：`on_conflict_do_nothing(constraint="uq_djg_doc_person")` 按唯一约束名跳过；与 `snapshot.py` 的 `getattr(result,"rowcount",0)` 模式一致（mypy strict 下 `Result` 无 `rowcount`）。

- [ ] **Step 2: 验证可导入且模型正确**

Run: `.venv/bin/python -c "from web_scraper_service.storage.djg_data import DjgData, DjgDataRepo, init_djg_table; print(DjgData.__tablename__, [c.name for c in DjgData.__table__.columns])"`
Expected: `djg_data ['id', 'doc_id', 'issue_date', 'issuing_authority', 'doc_number', 'institution_name', 'person_name', 'position', 'doc_title', 'doc_url', 'crawl_time']`

- [ ] **Step 3: mypy**

Run: `.venv/bin/python -m mypy src/web_scraper_service/storage/djg_data.py`
Expected: Success，无 error

- [ ] **Step 4: Commit**

```bash
git add src/web_scraper_service/storage/djg_data.py
git commit -m "feat: add djg_data storage layer for nfra extraction results"
```

---

### Task 3: 抽取模块（代码侧解析 + LLM）— TDD

**Files:**
- Create: `src/web_scraper_service/crawlers/nfra_extractor.py`
- Create: `tests/test_nfra/test_extractor.py`
- Test fixtures（已存在）：`tests/test_nfra/fixtures/doc_1258731_main.html`、`tests/test_nfra/fixtures/doc_1258343_jiangsu.html`

**Interfaces:**
- Consumes: `settings.dashscope_api_key`/`bailian_base_url`/`bailian_model`（Task 1）；`openai.AsyncOpenAI`；`tenacity`
- Produces:
  - `extract_meta(html: str, name: str) -> str`
  - `doc_title(html: str) -> str`
  - `issuing_authority(title: str) -> str`
  - `doc_number(html: str) -> str`
  - `clean_prose(html: str) -> str`
  - `SYSTEM_PROMPT: str`、`build_user_prompt(title: str, prose: str) -> str`
  - `parse_llm_rows(content: str) -> list[dict]`
  - `async extract_rows_llm(doc_id: int, html: str, doc_url: str) -> list[dict]`

- [ ] **Step 1: 写失败测试 tests/test_nfra/test_extractor.py**

```python
"""nfra 抽取模块单测（代码侧解析 + LLM 合并，mock 网络）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scraper_service.crawlers.nfra_extractor import (
    SYSTEM_PROMPT,
    build_user_prompt,
    clean_prose,
    doc_number,
    doc_title,
    extract_meta,
    extract_rows_llm,
    issuing_authority,
    parse_llm_rows,
)

FIXTURES = Path(__file__).parent / "fixtures"
MAIN_HTML = (FIXTURES / "doc_1258731_main.html").read_text(encoding="utf-8")
JS_HTML = (FIXTURES / "doc_1258343_jiangsu.html").read_text(encoding="utf-8")


def test_extract_meta() -> None:
    assert extract_meta(MAIN_HTML, "ArticleTitle").startswith("国家金融监督管理总局关于")


def test_doc_title() -> None:
    assert "姜亦峰" in doc_title(MAIN_HTML)
    assert "张伟" in doc_title(JS_HTML)


def test_issuing_authority() -> None:
    assert issuing_authority("国家金融监督管理总局关于X的批复") == "国家金融监督管理总局"
    assert issuing_authority("江苏金融监管局关于X的批复") == "江苏金融监管局"
    assert issuing_authority("无关于字样的标题") == "无关于字样的标题"


def test_doc_number_dom_path() -> None:
    assert doc_number(MAIN_HTML) == "金复〔2026〕240号"


def test_doc_number_prose_fallback() -> None:
    assert doc_number(JS_HTML) == "苏金复〔2026〕139号"


def test_doc_number_missing() -> None:
    assert doc_number("<html></html>") == ""


def test_clean_prose() -> None:
    p = clean_prose(MAIN_HTML)
    assert "太平洋健康保险股份有限公司" in p
    assert "<" not in p  # tags stripped


def test_build_user_prompt_contains_fields() -> None:
    prompt = build_user_prompt("某标题", "某正文")
    assert "person_name" in prompt
    assert "position" in prompt
    assert "某标题" in prompt
    assert "某正文" in prompt


def test_parse_llm_rows_valid() -> None:
    content = json.dumps({"rows": [
        {"person_name": "张伟", "position": "董事", "institution_name": "苏州银行股份有限公司", "issue_date": "2026年5月14日"}
    ]})
    rows = parse_llm_rows(content)
    assert len(rows) == 1
    assert rows[0]["person_name"] == "张伟"


def test_parse_llm_rows_empty() -> None:
    assert parse_llm_rows(json.dumps({"rows": []})) == []


def test_parse_llm_rows_invalid_json() -> None:
    assert parse_llm_rows("not json") == []


def test_parse_llm_rows_filters_invalid_name() -> None:
    content = json.dumps({"rows": [
        {"person_name": "", "position": "董事", "institution_name": "X", "issue_date": "Y"},
        {"person_name": "张伟", "position": "董事", "institution_name": "X", "issue_date": "Y"},
    ]})
    rows = parse_llm_rows(content)
    assert len(rows) == 1
    assert rows[0]["person_name"] == "张伟"


@pytest.mark.asyncio
async def test_extract_rows_llm_merges_code_and_llm_fields() -> None:
    """mock LLM 返回 6 行，校验合并后含代码侧字段。"""
    llm_content = json.dumps({"rows": [
        {"person_name": "张伟", "position": "董事", "institution_name": "苏州银行股份有限公司", "issue_date": "2026年5月14日"},
        {"person_name": "毛竹春", "position": "董事", "institution_name": "苏州银行股份有限公司", "issue_date": "2026年5月14日"},
    ]})
    fake_msg = MagicMock()
    fake_msg.message.content = llm_content
    fake_choice = MagicMock()
    fake_choice.message = fake_msg.message
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]

    with patch("web_scraper_service.crawlers.nfra_extractor.AsyncOpenAI") as MockClient:
        client_inst = MagicMock()
        client_inst.chat = MagicMock()
        client_inst.chat.completions = MagicMock()
        client_inst.chat.completions.create = AsyncMock(return_value=fake_resp)
        MockClient.return_value = client_inst

        rows = await extract_rows_llm(
            doc_id=1258343, html=JS_HTML,
            doc_url="https://www.nfra.gov.cn/branch/jiangsu/view/pages/common/ItemDetail.html?docId=1258343",
        )

    assert len(rows) == 2
    r = rows[0]
    assert r["doc_id"] == 1258343
    assert r["doc_title"].startswith("江苏金融监管局关于")
    assert r["issuing_authority"] == "江苏金融监管局"
    assert r["doc_number"] == "苏金复〔2026〕139号"
    assert r["person_name"] == "张伟"
    assert r["position"] == "董事"
    assert r["doc_url"].startswith("https://www.nfra.gov.cn/")


@pytest.mark.asyncio
async def test_extract_rows_llm_empty_rows_returns_empty() -> None:
    llm_content = json.dumps({"rows": []})
    fake_msg = MagicMock()
    fake_msg.message.content = llm_content
    fake_choice = MagicMock()
    fake_choice.message = fake_msg.message
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    with patch("web_scraper_service.crawlers.nfra_extractor.AsyncOpenAI") as MockClient:
        client_inst = MagicMock()
        client_inst.chat = MagicMock()
        client_inst.chat.completions = MagicMock()
        client_inst.chat.completions.create = AsyncMock(return_value=fake_resp)
        MockClient.return_value = client_inst
        rows = await extract_rows_llm(1, MAIN_HTML, "https://x")
    assert rows == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_nfra/test_extractor.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'web_scraper_service.crawlers.nfra_extractor'`）

- [ ] **Step 3: 写实现 src/web_scraper_service/crawlers/nfra_extractor.py**

```python
"""nfra 详情页抽取：代码侧选择器解析 + 百炼 LLM 抽取人/职务/机构/日期。

代码侧取 doc_title/issuing_authority/doc_number/clean_prose（可靠、省 token）；
person_name/position/institution_name/issue_date 交百炼 LLM。合并为 djg_data 行。
"""

from __future__ import annotations

import html as _html
import json
import re
from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from web_scraper_service.config import settings

# ── 代码侧解析 ───────────────────────────────────────────────


def extract_meta(html: str, name: str) -> str:
    """取 <meta name="..."> 的 content。"""
    m = re.search(rf'<meta\s+name="{name}"\s+content="([^"]*)">', html)
    return m.group(1) if m else ""


def doc_title(html: str) -> str:
    return extract_meta(html, "ArticleTitle")


def issuing_authority(title: str) -> str:
    """标题「关于」之前的部分；无「关于」则返回整标题。"""
    return title.split("关于", 1)[0] if "关于" in title else title


def doc_number(html: str) -> str:
    """发文函号：优先 DOM [ng-bind-html*="data.documentNo"]，回退正文开头。"""
    m = re.search(r'ng-bind-html="data\.documentNo[^"]*"[^>]*>([^<]*)<', html)
    if m and m.group(1).strip():
        return re.sub(r"\s+", "", m.group(1))
    # 回退：正文开头形如「苏金复〔2026〕139号」
    prose = clean_prose(html)
    norm = re.sub(r"\s+", "", prose)
    m2 = re.match(r"([一-龥A-Za-z]{1,8}〔\d{4}〕\d+号)", norm)
    return m2.group(1) if m2 else ""


def clean_prose(html: str) -> str:
    """提取 #wenzhang-content 正文，去 style/标签/多余空白。"""
    m = re.search(r'id="wenzhang-content"[^>]*>(.*)', html, re.S)
    body = m.group(1) if m else ""
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.S)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", text).strip()


# ── LLM 抽取 ─────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "你是一个金融监管文件信息抽取助手，专门从「金融机构人员任职资格批复」正文中抽取结构化信息。"
    "严格按规则抽取，只输出 JSON，不要任何解释或多余文字。"
)

_USER_TEMPLATE = """任务：从下方批复正文中，为每一位被核准任职资格的人员抽取一行记录。

批复标题：{title}
批复正文：
{prose}

抽取字段：
- person_name：人员姓名（2-4 个汉字，从「核准……的任职资格」句中提取）
- position：职务（核准其任职资格的岗位，如 董事/独立董事/监事/监事会主席/董事长/行长/副行长/总经理/副总经理 等，取原文措辞）
- institution_name：被批复的金融机构全称（如「苏州银行股份有限公司」，取正文收件人）
- issue_date：发文日期（正文末尾的中文日期，格式 YYYY年M月D日，如 2026年5月14日）

规则：
1. 一人一行。若一句「核准 A、B、C 等3人……董事的任职资格」核准多人同一职务，拆为多行，职务相同。
2. 若不同句核准不同职务（如有的任董事、有的任独立董事），各自取对应职务。
3. 人员姓名必须是真实人名，不得包含机构名、标点或「等N人」。
4. 若正文不属于人员任职资格批复（无「核准……任职资格」内容），返回 {{"rows": []}}。
5. 严格输出 JSON，schema：{{"rows":[{{"person_name":"","position":"","institution_name":"","issue_date":""}}]}}，无其他文字。

示例输入标题：江苏金融监管局关于张伟等6人苏州银行董事、独立董事任职资格的批复
示例输入正文：苏金复〔2026〕139号 苏州银行股份有限公司：……一、核准张伟、毛竹春、蒋亮等3人苏州银行股份有限公司董事的任职资格；核准夏平、赵欣、吴杰等3人苏州银行股份有限公司独立董事的任职资格。……2026年5月14日
示例输出：{{"rows":[{{"person_name":"张伟","position":"董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"毛竹春","position":"董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"蒋亮","position":"董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"夏平","position":"独立董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"赵欣","position":"独立董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"吴杰","position":"独立董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}}]}}"""


def build_user_prompt(title: str, prose: str) -> str:
    return _USER_TEMPLATE.format(title=title, prose=prose)


def parse_llm_rows(content: str) -> list[dict[str, str]]:
    """解析 LLM 返回的 JSON，校验并过滤非法行。"""
    try:
        payload: dict[str, Any] = json.loads(content)
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    rows = payload.get("rows") or []
    out: list[dict[str, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("person_name", "")).strip()
        # 只保留 2-4 汉字的合法人名
        if not re.fullmatch(r"[一-龥]{2,4}", name):
            continue
        out.append({
            "person_name": name,
            "position": str(r.get("position", "")).strip(),
            "institution_name": str(r.get("institution_name", "")).strip(),
            "issue_date": str(r.get("issue_date", "")).strip(),
        })
    return out


def _llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.bailian_base_url,
    )


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _call_llm(title: str, prose: str) -> str:
    client = _llm_client()
    resp = await client.chat.completions.create(
        model=settings.bailian_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(title, prose)},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


async def extract_rows_llm(doc_id: int, html: str, doc_url: str) -> list[dict[str, Any]]:
    """主入口：代码侧取结构化字段，LLM 取人/职务/机构/日期，合并为 djg_data 行。"""
    title = doc_title(html)
    prose = clean_prose(html)
    number = doc_number(html)
    authority = issuing_authority(title)
    code_fields = {
        "doc_id": doc_id,
        "doc_title": title,
        "doc_url": doc_url,
        "doc_number": number,
        "issuing_authority": authority,
    }
    try:
        content = await _call_llm(title, prose)
    except Exception as exc:
        logger.error("LLM 抽取失败 doc_id={}: {}", doc_id, exc)
        return []
    llm_rows = parse_llm_rows(content)
    return [{**code_fields, **r} for r in llm_rows]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_nfra/test_extractor.py -v`
Expected: 全部 PASS（13 项）

- [ ] **Step 5: ruff + mypy**

Run: `.venv/bin/ruff check src/web_scraper_service/crawlers/nfra_extractor.py tests/test_nfra/test_extractor.py && .venv/bin/python -m mypy src/web_scraper_service/crawlers/nfra_extractor.py`
Expected: 无 error

- [ ] **Step 6: Commit**

```bash
git add src/web_scraper_service/crawlers/nfra_extractor.py tests/test_nfra/test_extractor.py tests/test_nfra/fixtures/
git commit -m "feat: add nfra extractor (code-side parse + bailian LLM) with tests"
```

---

### Task 4: 列表标题过滤 + 详情 HTML URL + 列表 parse_doc_rows

**Files:**
- Modify: `src/web_scraper_service/crawlers/nfra.py`

**Interfaces:**
- Consumes: 现有 `parse_doc_ids`、`build_list_*`
- Produces:
  - `build_detail_html_url(doc_id: int) -> str`
  - `parse_doc_rows(body: str | bytes) -> list[dict]`（每项 `{"docId": int, "docTitle": str}`）
  - `async discover_doc_rows(session, item_id, pages) -> list[dict]`

- [ ] **Step 1: 在 nfra.py 加 build_detail_html_url**

在 `build_detail_url` 函数之后加：

```python
def build_detail_html_url(doc_id: int) -> str:
    return f"{BASE}/cn/view/pages/ItemDetail.html?docId={doc_id}&itemId=4111&generaltype=0"
```

- [ ] **Step 2: 在 nfra.py 加 parse_doc_rows（保留 parse_doc_ids）**

在 `parse_doc_ids` 函数之后加：

```python
def parse_doc_rows(body: str | bytes) -> list[dict[str, Any]]:
    """解析列表接口响应，返回 [{'docId': int, 'docTitle': str}, ...]。"""
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
    out: list[dict[str, Any]] = []
    for row in rows:
        doc_id = row.get("docId")
        if isinstance(doc_id, int):
            out.append({"docId": doc_id, "docTitle": str(row.get("docTitle") or "")})
    return out


async def discover_doc_rows(
    session: Any,
    item_id: int,
    pages: int,
) -> list[dict[str, Any]]:
    """用浏览器持久会话遍历列表 API，返回含标题的行。"""
    rows: list[dict[str, Any]] = []
    await session.fetch(build_list_html_url(item_id))
    for page in range(1, pages + 1):
        url = build_list_url(item_id, page)
        try:
            resp = await session.fetch(url, extra_headers=_LIST_HEADERS)
            page_rows = parse_doc_rows(resp.body)
        except Exception as exc:
            logger.warning("列表第 {} 页抓取失败: {}", page, exc)
            break
        if not page_rows:
            logger.info("列表第 {} 页无数据，停止翻页", page)
            break
        rows.extend(page_rows)
        logger.info("列表第 {} 页获得 {} 条，累计 {}", page, len(page_rows), len(rows))
        await asyncio.sleep(0.5)
    return rows
```

- [ ] **Step 3: 验证导入与签名**

Run: `.venv/bin/python -c "import inspect; from web_scraper_service.crawlers import nfra; print(hasattr(nfra,'build_detail_html_url'), hasattr(nfra,'parse_doc_rows'), hasattr(nfra,'discover_doc_rows'))"`
Expected: `True True True`

- [ ] **Step 4: ruff + mypy + 回归**

Run: `.venv/bin/ruff check src/web_scraper_service/crawlers/nfra.py && .venv/bin/python -m mypy src/web_scraper_service/crawlers/nfra.py && .venv/bin/python -m pytest tests/test_nfra/test_parse.py -q`
Expected: ruff/mypy 无 error；test_parse 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/web_scraper_service/crawlers/nfra.py
git commit -m "feat: add detail html url, parse_doc_rows, discover_doc_rows to nfra"
```

---

### Task 5: run_crawl 详情阶段改 DynamicSession + LLM + 写 djg_data

**Files:**
- Modify: `src/web_scraper_service/crawlers/nfra.py`（改 `run_crawl`，加 `_fetch_detail_rows`）

**Interfaces:**
- Consumes: Task 2 `DjgDataRepo`/`init_djg_table`/`SnapshotSession`；Task 3 `extract_rows_llm`；Task 4 `discover_doc_rows`/`build_detail_html_url`；`AsyncDynamicSession`
- Produces: 改写后的 `run_crawl`（统计 discovered/pending/extracted_rows/stored）

- [ ] **Step 1: 在 nfra.py 顶部补充 import**

在现有 `from web_scraper_service.storage.snapshot import (...)` 之后加：

```python
from web_scraper_service.crawlers.nfra_extractor import extract_rows_llm
from web_scraper_service.storage.djg_data import DjgDataRepo, init_djg_table
```

- [ ] **Step 2: 在 nfra.py 加 _fetch_detail_rows（替代 _fetch_one 用于新流程）**

在 `fetch_snapshots` 之后、`run_crawl` 之前加：

```python
async def _fetch_detail_rows(
    session: Any,
    doc_id: int,
    doc_url: str,
    download_delay: float,
) -> list[dict[str, Any]]:
    """打开详情 HTML，LLM 抽取，返回 djg_data 行列表。"""
    try:
        resp = await session.fetch(doc_url, network_idle=True, timeout=60000)
        html = resp.body.decode("utf-8", errors="replace") if isinstance(resp.body, bytes) else str(resp.body)
        rows = await extract_rows_llm(doc_id, html, doc_url)
        return rows
    except Exception as exc:
        logger.error("详情 doc_id={} 抽取失败: {}", doc_id, exc)
        return []
    finally:
        if download_delay > 0:
            await asyncio.sleep(download_delay)
```

- [ ] **Step 3: 改写 run_crawl**

将现有 `run_crawl` 函数整体替换为：

```python
async def run_crawl(
    item_id: int = 4110,
    pages: int = 5,
    concurrency: int = 2,
    download_delay: float = 1.0,
) -> dict[str, Any]:
    """完整编排：列表发现 → 标题过滤 → 跳过已存在 → 详情抽取 → 写 djg_data。"""
    from scrapling.fetchers import AsyncDynamicSession

    await init_djg_table()

    # 阶段 1：列表发现（含标题）
    async with AsyncStealthySession(headless=True) as session:
        rows = await discover_doc_rows(session, item_id, pages)
    logger.info("共发现 {} 条文档", len(rows))
    if not rows:
        return {"discovered": 0, "pending": 0, "extracted_rows": 0, "stored": 0}

    # 阶段 2：标题过滤（仅任职资格类）
    qualified = [r for r in rows if "任职资格" in r["docTitle"]]
    logger.info("标题含「任职资格」 {} 条", len(qualified))
    if not qualified:
        return {"discovered": len(rows), "pending": 0, "extracted_rows": 0, "stored": 0}

    # 阶段 3：跳过已存在
    pending_ids = [r["docId"] for r in qualified]
    async with SnapshotSession() as db:
        repo = DjgDataRepo(db)
        existing = await repo.existing_doc_ids(set(pending_ids))
    pending = [r for r in qualified if r["docId"] not in existing]
    logger.info("待抓取 {} 个（已存在 {} 个）", len(pending), len(existing))
    if not pending:
        return {"discovered": len(rows), "pending": 0, "extracted_rows": 0, "stored": 0}

    # 阶段 4：详情抽取（DynamicSession 持久浏览器 + LLM）
    sem = asyncio.Semaphore(concurrency)
    all_rows: list[dict[str, Any]] = []

    async def _guarded(r: dict[str, Any]) -> list[dict[str, Any]]:
        async with sem:
            return await _fetch_detail_rows(
                session, r["docId"], build_detail_html_url(r["docId"]), download_delay
            )

    async with AsyncDynamicSession(headless=True) as session:
        results = await asyncio.gather(*(_guarded(r) for r in pending))
    for batch in results:
        all_rows.extend(batch)
    logger.info("抽取行数 {} （来自 {} 个 doc）", len(all_rows), len(pending))

    # 阶段 5：写入 djg_data
    if not all_rows:
        return {"discovered": len(rows), "pending": len(pending), "extracted_rows": 0, "stored": 0}
    async with SnapshotSession() as db:
        repo = DjgDataRepo(db)
        stored = await repo.insert_many(all_rows)
    logger.info("写入 djg_data {} 行", stored)
    return {
        "discovered": len(rows),
        "pending": len(pending),
        "extracted_rows": len(all_rows),
        "stored": stored,
    }
```

注：`AsyncStealthySession` 仍用于列表发现（列表 API 需 JS cookie）；`AsyncDynamicSession` 用于详情 HTML（需 JS 渲染正文）。旧 `_fetch_one`/`fetch_snapshots` 不再被 `run_crawl` 调用，保留不删（避免破坏既有 import）。

- [ ] **Step 4: 验证签名 + 回归**

Run: `.venv/bin/python -c "import inspect; from web_scraper_service.crawlers.nfra import run_crawl; print(inspect.signature(run_crawl))"`
Expected: `(item_id: int = 4110, pages: int = 5, concurrency: int = 2, download_delay: float = 1.0) -> dict[str, Any]`

Run: `.venv/bin/python -m pytest tests/test_nfra/ -q`
Expected: test_parse + test_extractor 全 PASS

- [ ] **Step 5: ruff + mypy**

Run: `.venv/bin/ruff check src/web_scraper_service/crawlers/nfra.py && .venv/bin/python -m mypy src/web_scraper_service/crawlers/nfra.py`
Expected: 无 error

- [ ] **Step 6: Commit**

```bash
git add src/web_scraper_service/crawlers/nfra.py
git commit -m "feat: run_crawl extracts djg_data via DynamicSession + bailian LLM"
```

---

### Task 6: CLI 默认参数 + 手动 smoke 验收

**Files:**
- Modify: `scripts/crawl_nfra.py`

**前置：** `DASHSCOPE_API_KEY` 已写入 `.env`；Docker DB 在跑（`docker compose -f docker-compose.dev.yml up -d`）且 `zbd_crawler_data` 库已创建；patchright chromium 已装。

- [ ] **Step 1: 调整 scripts/crawl_nfra.py 默认参数**

将 `parse_args` 中的默认值改为：

```python
    parser.add_argument("--pages", type=int, default=5, help="采集最新页数（默认 5）")
    parser.add_argument("--item-id", type=int, default=4110, help="栏目 itemId（默认 4110）")
    parser.add_argument("--concurrency", type=int, default=2, help="详情并发数（默认 2，浏览器+LLM）")
    parser.add_argument("--download-delay", type=float, default=1.0, help="详情请求间隔秒（默认 1.0）")
```

- [ ] **Step 2: 验证 CLI 帮助**

Run: `.venv/bin/python scripts/crawl_nfra.py --help`
Expected: 输出参数，默认值 concurrency=2、download-delay=1.0

- [ ] **Step 3: 确认 DASHSCOPE_API_KEY 在 .env**

Run: `grep DASHSCOPE_API_KEY .env`
Expected: 非空 key（若空，smoke 会失败，需用户填入后继续）

- [ ] **Step 4: 小规模实跑（1 页）**

Run: `timeout 400 .venv/bin/python scripts/crawl_nfra.py --pages 1`
Expected: 日志依次出现「列表第 1 页获得 N 条」「标题含「任职资格」 M 条」「待抓取 K 个」「抽取行数 X」「写入 djg_data X 行」；退出码 0。若 LLM 返回 429/模型 id 报错，记录错误信息。

- [ ] **Step 5: 验证 djg_data 落库**

Run:
```bash
.venv/bin/python - <<'EOF'
import asyncio, asyncpg
async def main():
    conn = await asyncpg.connect("postgresql://scraper:scraper_secret@localhost:5432/zbd_crawler_data")
    cnt = await conn.fetchval("SELECT count(*) FROM djg_data")
    print("djg_data rows:", cnt)
    for r in await conn.fetch("SELECT doc_id, person_name, position, institution_name, issue_date FROM djg_data LIMIT 10"):
        print(" ", dict(r))
    await conn.close()
asyncio.run(main())
EOF
```
Expected: 行数 > 0；字段值合理（person_name 2-4 汉字，position 含「董事」等）

- [ ] **Step 6: 验证 skip 语义（重跑）**

Run: `timeout 200 .venv/bin/python scripts/crawl_nfra.py --pages 1`
Expected: 日志「待抓取 0 个」（已存在过滤），stored=0

- [ ] **Step 7: 全量测试套件无回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全 PASS（test_parse + test_extractor + 既有）

- [ ] **Step 8: Commit**

```bash
git add scripts/crawl_nfra.py
git commit -m "chore: tune crawl-nfra defaults for browser+LLM flow"
```

---

## Self-Review 记录

- **Spec 覆盖**：openai+配置(Task1)✓；djg_data 存储(Task2)✓；hybrid 抽取+prompt(Task3)✓；标题过滤+parse_doc_rows+detail html url(Task4)✓；run_crawl 改 DynamicSession+LLM+写 djg_data(Task5)✓；CLI+smoke(Task6)✓；跳过语义(Task2 repo + Task5 run_crawl)✓；ON CONFLICT (doc_id,person_name)(Task2)✓；web_snapshot 不动(Task5 注释)✓。
- **API 已验证**：`AsyncDynamicSession` 为 async ctx mgr（`__aenter__`/`start`/`close`），`await session.fetch(url, network_idle=True, timeout=60000)` 可用；`AsyncOpenAI`（openai>=1.50）`chat.completions.create(response_format={"type":"json_object"})` 返回 `resp.choices[0].message.content`；`doc_number` 两策略（DOM + prose 回退）在两 fixture 验证通过；`AsyncStealthySession` 仍用于列表（已验证）。
- **类型一致**：`DjgDataRepo.existing_doc_ids(set[int])->set[int]`、`insert_many(list[dict])->int` 在 Task2/Task5 调用一致；`extract_rows_llm(doc_id:int, html:str, doc_url:str)->list[dict]` 在 Task3/Task5 一致；`parse_doc_rows(body)->list[dict]`、`discover_doc_rows(session,item_id,pages)->list[dict]`、`build_detail_html_url(doc_id)->str` 在 Task4/Task5 一致；`run_crawl` 统计键 discovered/pending/extracted_rows/stored 在 Task5/Task6 一致。
- **无占位符**：所有步骤含完整代码与确切命令；prompt 完整内联；fixture 已存在于仓库。
