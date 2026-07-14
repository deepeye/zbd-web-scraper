# djg_data Publish Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `publish_date DATE` column to `djg_data` and populate it from nfra detail pages by extracting the `发布时间：YYYY-MM-DD` date from rendered HTML.

**Architecture:** Keep publish-date extraction in deterministic Python code, not the LLM prompt. The extractor returns a `datetime.date | None`, merges that value into every per-person row for the document, and the storage/API layers persist and serialize it.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async ORM, PostgreSQL, pytest, Scrapling-rendered HTML fixtures.

## Global Constraints

- Field name is exactly `publish_date`.
- Database type is PostgreSQL `DATE` via SQLAlchemy `Date`.
- Source text is rendered detail HTML containing `发布时间：YYYY-MM-DD`.
- Do not depend on the LLM for `publish_date`.
- Existing `issue_date` remains unchanged and continues to represent the approval/effective date extracted by LLM.
- Existing `crawl_time` remains unchanged and continues to represent collection time.
- Existing deployments must self-heal with `ALTER TABLE djg_data ADD COLUMN IF NOT EXISTS publish_date DATE` inside `init_djg_table()`.

---

## File Structure

- Modify `src/web_scraper_service/crawlers/nfra_extractor.py` — add `publish_date(html: str) -> date | None`, include it in code-side fields returned by `extract_rows_llm()`.
- Modify `src/web_scraper_service/storage/djg_data.py` — add SQLAlchemy `Date` import, `DjgData.publish_date`, and an idempotent schema upgrade in `init_djg_table()`.
- Modify `src/web_scraper_service/api/v1/nfra.py` — include `publish_date` in `/api/v1/nfra/data` response rows as ISO string or `None`.
- Modify `tests/test_nfra/test_extractor.py` — cover parsing from fixtures and row merge behavior.
- Modify `tests/test_api/test_nfra.py` — cover API serialization of `publish_date`.
- Create `tests/test_nfra/test_djg_data_storage.py` — verify `init_djg_table()` issues the `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` DDL after `create_all`.

---

### Task 1: Extract Publish Date From Rendered HTML

**Files:**
- Modify: `src/web_scraper_service/crawlers/nfra_extractor.py:9-176`
- Modify: `tests/test_nfra/test_extractor.py:5-129`

**Interfaces:**
- Consumes: rendered detail HTML string from `extract_rows_llm(doc_id: int, html: str, doc_url: str)`.
- Produces: `publish_date(html: str) -> date | None`; `extract_rows_llm()` rows include key `"publish_date"` with `date | None`.

- [ ] **Step 1: Write failing parser tests**

Add `date` import and import `publish_date` in `tests/test_nfra/test_extractor.py`:

```python
from datetime import date
```

```python
from web_scraper_service.crawlers.nfra_extractor import (
    SYSTEM_PROMPT,  # noqa: F401
    build_user_prompt,
    clean_prose,
    doc_number,
    doc_title,
    extract_meta,
    extract_rows_llm,
    issuing_authority,
    parse_llm_rows,
    publish_date,
)
```

Add tests after `test_extract_meta()`:

```python
def test_publish_date_from_rendered_detail_text() -> None:
    assert publish_date(MAIN_HTML) == date(2026, 5, 8)
    assert publish_date(JS_HTML) == date(2026, 5, 14)


def test_publish_date_missing_returns_none() -> None:
    assert publish_date("<html><body>无发布时间</body></html>") is None
```

- [ ] **Step 2: Run parser tests and verify failure**

Run: `uv run pytest tests/test_nfra/test_extractor.py::test_publish_date_from_rendered_detail_text tests/test_nfra/test_extractor.py::test_publish_date_missing_returns_none -v`

Expected: FAIL because `publish_date` is not importable from `web_scraper_service.crawlers.nfra_extractor`.

- [ ] **Step 3: Implement parser**

Modify imports in `src/web_scraper_service/crawlers/nfra_extractor.py`:

```python
from datetime import date
```

Add this function after `extract_meta()`:

```python
def publish_date(html: str) -> date | None:
    match = re.search(r"发布时间\s*[:：]\s*(\d{4})-(\d{2})-(\d{2})", html)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)
```

- [ ] **Step 4: Verify parser tests pass**

Run: `uv run pytest tests/test_nfra/test_extractor.py::test_publish_date_from_rendered_detail_text tests/test_nfra/test_extractor.py::test_publish_date_missing_returns_none -v`

Expected: PASS for both tests.

- [ ] **Step 5: Write failing merge assertion**

In `test_extract_rows_llm_merges_code_and_llm_fields()`, add this assertion after `assert r["doc_number"] == "苏金复〔2026〕139号"`:

```python
    assert r["publish_date"] == date(2026, 5, 14)
```

- [ ] **Step 6: Run merge test and verify failure**

Run: `uv run pytest tests/test_nfra/test_extractor.py::test_extract_rows_llm_merges_code_and_llm_fields -v`

Expected: FAIL with `KeyError: 'publish_date'`.

- [ ] **Step 7: Merge publish date into extracted rows**

In `extract_rows_llm()`, add `publish_date` to `code_fields`:

```python
    code_fields = {
        "doc_id": doc_id,
        "doc_title": title,
        "doc_url": doc_url,
        "doc_number": number,
        "issuing_authority": authority,
        "publish_date": publish_date(html),
    }
```

- [ ] **Step 8: Verify extractor tests pass**

Run: `uv run pytest tests/test_nfra/test_extractor.py -v`

Expected: PASS.

- [ ] **Step 9: Commit extractor changes**

```bash
git add src/web_scraper_service/crawlers/nfra_extractor.py tests/test_nfra/test_extractor.py
git commit -m "feat(nfra): extract publish date from detail pages"
```

---

### Task 2: Persist Publish Date In djg_data

**Files:**
- Modify: `src/web_scraper_service/storage/djg_data.py:9-44`
- Create: `tests/test_nfra/test_djg_data_storage.py`

**Interfaces:**
- Consumes: row dictionaries containing `publish_date: date | None` from Task 1.
- Produces: `DjgData.publish_date: Mapped[date | None]` stored in PostgreSQL `DATE`; `init_djg_table() -> None` creates or upgrades the column.

- [ ] **Step 1: Write failing model/DDL tests**

Create `tests/test_nfra/test_djg_data_storage.py`:

```python
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Date

from web_scraper_service.storage.djg_data import DjgData, init_djg_table


def test_djg_data_publish_date_column_is_date() -> None:
    column = DjgData.__table__.c.publish_date
    assert isinstance(column.type, Date)
    assert column.nullable is True


@pytest.mark.asyncio
async def test_init_djg_table_adds_publish_date_column() -> None:
    conn = MagicMock()
    conn.run_sync = AsyncMock()
    conn.execute = AsyncMock()
    begin_context = AsyncMock()
    begin_context.__aenter__.return_value = conn
    begin_context.__aexit__.return_value = None

    with patch("web_scraper_service.storage.djg_data.snapshot_engine.begin", return_value=begin_context):
        await init_djg_table()

    conn.execute.assert_awaited_once()
    ddl = str(conn.execute.await_args.args[0])
    assert "ALTER TABLE djg_data" in ddl
    assert "ADD COLUMN IF NOT EXISTS publish_date DATE" in ddl
```

- [ ] **Step 2: Run storage tests and verify failure**

Run: `uv run pytest tests/test_nfra/test_djg_data_storage.py -v`

Expected: FAIL because `DjgData.__table__.c.publish_date` does not exist.

- [ ] **Step 3: Add ORM column and schema upgrade**

Modify imports in `src/web_scraper_service/storage/djg_data.py`:

```python
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Text, UniqueConstraint, func, select, text
```

Add the mapped column after `doc_id`:

```python
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
```

Update `init_djg_table()`:

```python
async def init_djg_table() -> None:
    """CREATE TABLE IF NOT EXISTS djg_data."""
    async with snapshot_engine.begin() as conn:
        await conn.run_sync(_DjgBase.metadata.create_all)
        await conn.execute(text("ALTER TABLE djg_data ADD COLUMN IF NOT EXISTS publish_date DATE"))
```

- [ ] **Step 4: Verify storage tests pass**

Run: `uv run pytest tests/test_nfra/test_djg_data_storage.py -v`

Expected: PASS.

- [ ] **Step 5: Verify insert path accepts publish_date**

Run: `uv run pytest tests/test_nfra/test_extractor.py::test_extract_rows_llm_merges_code_and_llm_fields tests/test_nfra/test_djg_data_storage.py -v`

Expected: PASS.

- [ ] **Step 6: Commit storage changes**

```bash
git add src/web_scraper_service/storage/djg_data.py tests/test_nfra/test_djg_data_storage.py
git commit -m "feat(nfra): persist publish date in djg_data"
```

---

### Task 3: Return Publish Date From nfra Data API

**Files:**
- Modify: `src/web_scraper_service/api/v1/nfra.py:64-97`
- Modify: `tests/test_api/test_nfra.py:5-226`

**Interfaces:**
- Consumes: `DjgData.publish_date: date | None` from Task 2.
- Produces: `/api/v1/nfra/data` response rows include `"publish_date": "YYYY-MM-DD" | None`.

- [ ] **Step 1: Write failing API serialization test updates**

Modify imports in `tests/test_api/test_nfra.py`:

```python
from datetime import date, datetime, timezone
```

Add parameter to `_fake_row()` after `doc_id`:

```python
    publish_date: date | None = date(2026, 5, 14),
```

Set it before `issue_date`:

```python
    r.publish_date = publish_date
```

Add assertion in `test_get_data_with_date_range()` after `assert rows[0]["doc_id"] == 1258343`:

```python
    assert rows[0]["publish_date"] == "2026-05-14"
```

Add a focused null serialization test after `test_get_data_with_date_range()`:

```python
def test_get_data_publish_date_null(client: TestClient, _api_key: str) -> None:
    repo = MagicMock()
    repo.list_by_crawl_time = AsyncMock(return_value=[_fake_row(publish_date=None)])
    repo.count_by_crawl_time = AsyncMock(return_value=1)
    client.app.dependency_overrides[get_djg_data_repo] = lambda: repo
    try:
        resp = client.get("/api/v1/nfra/data", headers={"X-API-Key": _api_key})
    finally:
        client.app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["data"][0]["publish_date"] is None
```

- [ ] **Step 2: Run API tests and verify failure**

Run: `uv run pytest tests/test_api/test_nfra.py::test_get_data_with_date_range tests/test_api/test_nfra.py::test_get_data_publish_date_null -v`

Expected: first test FAILS with missing `publish_date` key; second test FAILS until API includes the key.

- [ ] **Step 3: Serialize publish_date in API response**

In `src/web_scraper_service/api/v1/nfra.py`, add `publish_date` after `doc_id` in the response row dict:

```python
                "publish_date": r.publish_date.isoformat() if r.publish_date else None,
```

- [ ] **Step 4: Verify API tests pass**

Run: `uv run pytest tests/test_api/test_nfra.py -v`

Expected: PASS.

- [ ] **Step 5: Commit API changes**

```bash
git add src/web_scraper_service/api/v1/nfra.py tests/test_api/test_nfra.py
git commit -m "feat(nfra): return publish date in data API"
```

---

### Task 4: Final Regression Check

**Files:**
- Test only: `tests/test_nfra/test_extractor.py`
- Test only: `tests/test_nfra/test_djg_data_storage.py`
- Test only: `tests/test_api/test_nfra.py`

**Interfaces:**
- Consumes: Tasks 1-3 completed.
- Produces: verified publish-date extraction, persistence, and API serialization.

- [ ] **Step 1: Run focused nfra regression tests**

Run:

```bash
uv run pytest tests/test_nfra/test_extractor.py tests/test_nfra/test_djg_data_storage.py tests/test_api/test_nfra.py -v
```

Expected: PASS.

- [ ] **Step 2: Run lint on touched files**

Run:

```bash
uv run ruff check src/web_scraper_service/crawlers/nfra_extractor.py src/web_scraper_service/storage/djg_data.py src/web_scraper_service/api/v1/nfra.py tests/test_nfra/test_extractor.py tests/test_nfra/test_djg_data_storage.py tests/test_api/test_nfra.py
```

Expected: PASS.

- [ ] **Step 3: Run type check if project dependencies are available**

Run:

```bash
uv run mypy src/web_scraper_service/crawlers/nfra_extractor.py src/web_scraper_service/storage/djg_data.py src/web_scraper_service/api/v1/nfra.py
```

Expected: PASS, or only pre-existing unrelated project-level mypy failures. Do not change unrelated files to fix unrelated failures.

- [ ] **Step 4: Inspect working tree**

Run:

```bash
git status --short
```

Expected: only the intended files are changed if commits were not made; clean working tree if each task was committed.

---

## Self-Review

- Spec coverage: `publish_date` field, SQL `DATE` type, code-side extraction from `发布时间：YYYY-MM-DD`, idempotent schema upgrade, extractor merge, and API serialization are all covered by Tasks 1-3.
- Placeholder scan: no undefined implementation steps remain; every code-changing step includes exact code and commands.
- Type consistency: `publish_date` is consistently `date | None` in Python, SQLAlchemy `Date` in storage, and ISO string or `None` in API JSON.
