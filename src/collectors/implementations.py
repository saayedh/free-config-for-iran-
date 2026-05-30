"""
Concrete collector implementations.
Each collector is self-contained — add new ones by subclassing BaseCollector.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from src.collectors.base import BaseCollector, ParseError
from src.processors.schemas import EntryType, NormalizedEntry, RawEntry


class JsonApiCollector(BaseCollector):
    """
    Collects entries from a JSON API endpoint.
    Expects a JSON array or an object with an 'items'/'data'/'results' key.
    """

    source_id = "json_api_example"
    source_name = "JSON API Example"
    source_url = "https://example.com/api/v1/items"

    def __init__(self, data_key: str = "data", content_field: str = "content") -> None:
        super().__init__()
        self._data_key = data_key
        self._content_field = content_field

    async def parse(self, raw: RawEntry) -> list[NormalizedEntry]:
        try:
            payload: Any = json.loads(raw.raw_content)
        except json.JSONDecodeError as exc:
            raise ParseError(f"Invalid JSON from {self.source_id}: {exc}") from exc

        items: list[Any] = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            for key in (self._data_key, "items", "results", "entries"):
                if key in payload and isinstance(payload[key], list):
                    items = payload[key]
                    break

        if not items:
            return []

        entries: list[NormalizedEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = item.get(self._content_field, "")
            if not content:
                continue
            entries.append(
                NormalizedEntry(
                    source_id=self.source_id,
                    entry_type=EntryType.GENERIC,
                    title=item.get("title") or item.get("name"),
                    description=item.get("description") or item.get("summary"),
                    content=str(content),
                    tags=item.get("tags", []),
                    fetched_at=raw.fetched_at,
                    source_url=raw.source_url,
                    extra={k: v for k, v in item.items() if k not in ("title", "content")},
                )
            )
        return entries


class HtmlPageCollector(BaseCollector):
    """
    Collects entries by scraping HTML pages with BeautifulSoup.
    Override `_extract_items` to customize per-site logic.
    """

    source_id = "html_page_example"
    source_name = "HTML Page Example"
    source_url = "https://example.com/list"
    _item_selector = "article"
    _content_selector = "p"
    _title_selector = "h2"

    async def parse(self, raw: RawEntry) -> list[NormalizedEntry]:
        soup = BeautifulSoup(raw.raw_content, "lxml")
        items = soup.select(self._item_selector)
        entries: list[NormalizedEntry] = []

        for item in items:
            title_el = item.select_one(self._title_selector)
            content_el = item.select_one(self._content_selector)
            content = content_el.get_text(strip=True) if content_el else ""
            if not content:
                continue
            entries.append(
                NormalizedEntry(
                    source_id=self.source_id,
                    entry_type=EntryType.GENERIC,
                    title=title_el.get_text(strip=True) if title_el else None,
                    content=content,
                    fetched_at=raw.fetched_at,
                    source_url=raw.source_url,
                )
            )
        return entries


class PlainTextCollector(BaseCollector):
    """
    Collects entries from a plain-text endpoint, one entry per line.
    Useful for simple list-based sources.
    """

    source_id = "plaintext_example"
    source_name = "Plain Text Example"
    source_url = "https://example.com/list.txt"
    _line_pattern: re.Pattern[str] | None = None  # override to filter lines

    async def parse(self, raw: RawEntry) -> list[NormalizedEntry]:
        lines = [line.strip() for line in raw.raw_content.splitlines() if line.strip()]
        if self._line_pattern:
            lines = [l for l in lines if self._line_pattern.match(l)]

        return [
            NormalizedEntry(
                source_id=self.source_id,
                entry_type=EntryType.GENERIC,
                content=line,
                fetched_at=raw.fetched_at,
                source_url=raw.source_url,
            )
            for line in lines
        ]


# ── Collector Registry ──────────────────────────────────────────────────────

COLLECTOR_REGISTRY: dict[str, type[BaseCollector]] = {
    JsonApiCollector.source_id: JsonApiCollector,
    HtmlPageCollector.source_id: HtmlPageCollector,
    PlainTextCollector.source_id: PlainTextCollector,
}


def get_collector(source_id: str) -> BaseCollector:
    """Instantiate a collector by source_id. Raises KeyError if not registered."""
    cls = COLLECTOR_REGISTRY[source_id]
    return cls()
