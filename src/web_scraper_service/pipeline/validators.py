"""Pydantic v2 models for scrape result validation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class BaseItem(BaseModel):
    """Base item model — all spider items should inherit from this."""

    url: str
    spider_name: str = ""
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("url")
    @classmethod
    def url_must_be_valid(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            msg = f"Invalid URL: {v}"
            raise ValueError(msg)
        return v.strip()


class QuoteItem(BaseItem):
    """Example item model for the quotes spider."""

    text: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("text", "author")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, v: list[str]) -> list[str]:
        return [t.strip() for t in v if t.strip()]


# Registry of item models per spider name
ITEM_MODELS: dict[str, type[BaseItem]] = {
    "quotes_static": QuoteItem,
    "quotes_spa": QuoteItem,
}


def get_item_model(spider_name: str) -> type[BaseItem]:
    return ITEM_MODELS.get(spider_name, BaseItem)


def validate_item(spider_name: str, data: dict[str, Any]) -> BaseItem:
    model = get_item_model(spider_name)
    return model(**data)
