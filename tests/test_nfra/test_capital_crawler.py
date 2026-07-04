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
        def __init__(self, *args, **kwargs):
            pass

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
        def __init__(self, *args, **kwargs):
            pass

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
