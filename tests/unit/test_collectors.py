"""
Unit tests for collectors using respx to mock HTTP.
"""

from __future__ import annotations

import json
import pytest
import respx
import httpx
from datetime import datetime, timezone

from src.collectors.implementations import JsonApiCollector, PlainTextCollector
from src.processors.schemas import RawEntry


@pytest.mark.asyncio
async def test_json_collector_parses_items():
    payload = {
        "data": [
            {"content": "Entry one with enough text to pass validation", "title": "One"},
            {"content": "Entry two with enough text to pass validation", "title": "Two"},
        ]
    }

    with respx.mock:
        respx.get(JsonApiCollector.source_url).mock(
            return_value=httpx.Response(200, json=payload)
        )
        collector = JsonApiCollector()
        entries = await collector.collect()

    assert len(entries) == 2
    assert entries[0].title == "One"
    assert entries[1].content == "Entry two with enough text to pass validation"


@pytest.mark.asyncio
async def test_json_collector_handles_empty_response():
    with respx.mock:
        respx.get(JsonApiCollector.source_url).mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        collector = JsonApiCollector()
        entries = await collector.collect()

    assert entries == []


@pytest.mark.asyncio
async def test_json_collector_handles_http_error():
    with respx.mock:
        respx.get(JsonApiCollector.source_url).mock(
            return_value=httpx.Response(500)
        )
        collector = JsonApiCollector()
        with pytest.raises(Exception):
            await collector.collect()


@pytest.mark.asyncio
async def test_plaintext_collector_splits_lines():
    text = "line one\nline two\nline three\n\n"

    with respx.mock:
        respx.get(PlainTextCollector.source_url).mock(
            return_value=httpx.Response(200, text=text)
        )
        collector = PlainTextCollector()
        entries = await collector.collect()

    assert len(entries) == 3
    assert entries[0].content == "line one"
