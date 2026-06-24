from __future__ import annotations

from typing import Any

from loguru import logger

from web_scraper_service.spiders.base import BaseSpider

_registry: dict[str, type[BaseSpider]] = {}


def register_spider(cls: type[BaseSpider]) -> type[BaseSpider]:
    """Decorator to register a spider class."""
    name = cls.name or cls.__name__
    if name in _registry:
        logger.warning("Spider '{name}' already registered, overwriting", name=name)
    _registry[name] = cls
    return cls


def get_spider_class(name: str) -> type[BaseSpider] | None:
    return _registry.get(name)


def list_spiders() -> list[str]:
    return list(_registry.keys())


def create_spider(name: str, **kwargs: Any) -> BaseSpider:
    cls = _registry.get(name)
    if not cls:
        raise ValueError(f"Spider '{name}' not found. Available: {list_spiders()}")
    return cls(**kwargs)
