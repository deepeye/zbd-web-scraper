"""Tests for the spider registry."""

from __future__ import annotations

from web_scraper_service.spiders.registry import create_spider, list_spiders


def test_list_spiders() -> None:
    # Import examples to register them
    import web_scraper_service.spiders.examples.static_spider  # noqa: F401
    import web_scraper_service.spiders.examples.spa_spider  # noqa: F401

    names = list_spiders()
    assert "quotes_static" in names
    assert "quotes_spa" in names


def test_create_spider() -> None:
    import web_scraper_service.spiders.examples.static_spider  # noqa: F401

    spider = create_spider("quotes_static")
    assert spider.name == "quotes_static"
    assert spider.use_playwright is False


def test_create_unknown_spider() -> None:
    import pytest

    with pytest.raises(ValueError, match="not found"):
        create_spider("nonexistent")
