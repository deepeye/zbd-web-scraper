"""nfra crawl API tests (mock Celery)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from web_scraper_service.config import settings
from web_scraper_service.main import app


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
