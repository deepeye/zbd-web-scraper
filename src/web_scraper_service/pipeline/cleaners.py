"""Chain-of-responsibility data cleaners."""

from __future__ import annotations

import html
import re
from typing import Any, Callable


class Cleaner:
    """Base cleaner — override clean() to implement a cleaning step."""

    def clean(self, value: Any) -> Any:
        return value


class WhitespaceCleaner(Cleaner):
    """Collapse whitespace and strip."""

    def clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return re.sub(r"\s+", " ", value).strip()
        return value


class HTMLEntityCleaner(Cleaner):
    """Decode HTML entities like &amp; &nbsp;."""

    def clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return html.unescape(value)
        return value


class DateNormalizer(Cleaner):
    """Normalize common date formats to ISO 8601."""

    _DATE_PATTERNS: list[tuple[str, str]] = [
        (r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", r"\1-\2-\3"),
        (r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", r"\3-\1-\2"),
    ]

    def clean(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        for pattern, replacement in self._DATE_PATTERNS:
            result = re.sub(pattern, replacement, value)
            if result != value:
                return result
        return value


class EmailExtractor(Cleaner):
    """Extract email addresses from text."""

    _EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

    def clean(self, value: Any) -> Any:
        if isinstance(value, str):
            found = self._EMAIL_RE.findall(value)
            return found[0] if len(found) == 1 else found
        return value


class PhoneExtractor(Cleaner):
    """Extract phone numbers from text."""

    _PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")

    def clean(self, value: Any) -> Any:
        if isinstance(value, str):
            found = self._PHONE_RE.findall(value)
            return found[0] if len(found) == 1 else found
        return value


class CleaningPipeline:
    """Chain multiple cleaners together."""

    def __init__(self, cleaners: list[Cleaner] | None = None) -> None:
        self.cleaners: list[Cleaner] = cleaners or [
            HTMLEntityCleaner(),
            WhitespaceCleaner(),
        ]

    def add(self, cleaner: Cleaner) -> "CleaningPipeline":
        self.cleaners.append(cleaner)
        return self

    def process(self, data: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in data.items():
            for cleaner in self.cleaners:
                value = cleaner.clean(value)
            cleaned[key] = value
        return cleaned


# Pre-configured default pipeline
default_pipeline = CleaningPipeline()
