"""nfra crawl API tests (mock Celery)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from web_scraper_service.api.deps import get_djg_data_repo
from web_scraper_service.config import settings
from web_scraper_service.main import app
from web_scraper_service.storage.djg_data import DjgData


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


@pytest.mark.asyncio
async def test_init_nfra_schedule_registers_both_itemids(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_scraper_service.scheduler import engine

    monkeypatch.setattr(engine.settings, "nfra_schedule_enabled", True)
    monkeypatch.setattr(engine.settings, "nfra_schedule_cron", "0 8 * * *")
    monkeypatch.setattr(engine.settings, "nfra_schedule_pages", 5)

    sched = MagicMock()
    added: list[dict[str, Any]] = []

    def fake_add_job(func, *, trigger, id, name, replace_existing):  # noqa: A002
        added.append({"id": id, "name": name, "trigger": trigger})
        func()  # simulate scheduler firing the job so dispatch is observable
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
    # APScheduler CronTrigger stores fields in _fields (0=minute, 1=hour).
    # Use str() for robustness across APScheduler versions.
    assert "8" in str(added[0]["trigger"])  # hour=8
    assert dispatched == [(4110, 5), (4291, 5)]


@pytest.mark.asyncio
async def test_init_nfra_schedule_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_scraper_service.scheduler import engine

    monkeypatch.setattr(engine.settings, "nfra_schedule_enabled", False)
    sched = MagicMock()
    monkeypatch.setattr(engine, "_scheduler", sched)
    await engine.init_nfra_schedule()
    sched.add_job.assert_not_called()


def _fake_row(
    *,
    id: int = 1,  # noqa: A002
    doc_id: int = 1258343,
    publish_date: date | None = date(2026, 5, 14),
    person_name: str = "张伟",
    position: str = "董事",
    institution_name: str = "苏州银行股份有限公司",
    issue_date: str = "2026年5月14日",
    issuing_authority: str = "江苏金融监管局",
    doc_number: str = "苏金复〔2026〕139号",
    doc_title: str = "江苏金融监管局关于张伟等6人苏州银行董事、独立董事任职资格的批复",
    doc_url: str = "https://www.nfra.gov.cn/x",
    crawl_time: datetime | None = None,
) -> DjgData:
    r = DjgData()
    r.id = id
    r.doc_id = doc_id
    r.publish_date = publish_date
    r.person_name = person_name
    r.position = position
    r.institution_name = institution_name
    r.issue_date = issue_date
    r.issuing_authority = issuing_authority
    r.doc_number = doc_number
    r.doc_title = doc_title
    r.doc_url = doc_url
    r.crawl_time = crawl_time or datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)  # noqa: UP017
    return r


def test_get_data_with_date_range(
    client: TestClient,
    _api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = MagicMock()
    repo.list_by_crawl_time = AsyncMock(return_value=[_fake_row(id=1), _fake_row(id=2)])
    repo.count_by_crawl_time = AsyncMock(return_value=2)
    client.app.dependency_overrides[get_djg_data_repo] = lambda: repo
    try:
        resp = client.get(
            "/api/v1/nfra/data",
            params={
                "start_date": "2026-06-25T00:00:00",
                "end_date": "2026-06-26T00:00:00",
                "page": 1,
                "size": 20,
            },
            headers={"X-API-Key": _api_key},
        )
    finally:
        client.app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    rows = body["data"]
    assert len(rows) == 2
    assert rows[0]["person_name"] == "张伟"
    assert rows[0]["doc_id"] == 1258343
    assert rows[0]["publish_date"] == "2026-05-14"
    assert "crawl_time" in rows[0]
    assert body["pagination"]["total"] == 2
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["size"] == 20
    repo.list_by_crawl_time.assert_awaited_once()
    args, kwargs = repo.list_by_crawl_time.call_args
    assert kwargs["limit"] == 20
    assert kwargs["offset"] == 0


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
