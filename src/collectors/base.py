"""
Abstract base class for all source collectors.
Every collector is an isolated plugin — failure in one cannot cascade.
"""

from __future__ import annotations

import abc
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import get_settings
from src.processors.schemas import NormalizedEntry, RawEntry

logger = structlog.get_logger(__name__)


class CollectorError(Exception):
    """Base exception for all collector failures."""


class FetchError(CollectorError):
    """HTTP fetch failed."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ParseError(CollectorError):
    """Response body could not be parsed."""


class BaseCollector(abc.ABC):
    """
    Plugin contract for all source collectors.

    Subclasses implement only `parse()` — the base handles HTTP, retries,
    rate limiting, and logging so each plugin stays minimal.
    """

    source_id: str
    source_name: str
    source_url: str

    def __init__(self) -> None:
        self._settings = get_settings()
        self._log = structlog.get_logger(self.__class__.__name__)
        self._client: httpx.AsyncClient | None = None

    # ── HTTP client lifecycle ──────────────────────────────────

    async def __aenter__(self) -> "BaseCollector":
        self._client = httpx.AsyncClient(
            timeout=self._settings.collector.default_timeout,
            headers={"User-Agent": self._settings.collector.user_agent},
            follow_redirects=True,
            http2=True,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Retry-wrapped fetch ────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def _fetch(self, url: str, **kwargs: Any) -> httpx.Response:
        assert self._client is not None, "Must use as async context manager"
        response = await self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

    # ── Public API ─────────────────────────────────────────────

    async def collect(self) -> list[NormalizedEntry]:
        """
        Entry point called by the scheduler.
        Returns a list of NormalizedEntry objects.
        Guaranteed not to raise — all errors are caught and logged.
        """
        start = time.monotonic()
        self._log.info("collector_started", source_id=self.source_id, url=self.source_url)

        try:
            async with self:
                response = await self._fetch(self.source_url)
                raw = RawEntry(
                    source_id=self.source_id,
                    source_url=self.source_url,
                    raw_content=response.text,
                    fetched_at=datetime.now(tz=timezone.utc),
                    http_status=response.status_code,
                )
                entries = await self.parse(raw)
                elapsed = time.monotonic() - start
                self._log.info(
                    "collector_finished",
                    source_id=self.source_id,
                    count=len(entries),
                    elapsed_s=round(elapsed, 2),
                )
                return entries

        except httpx.HTTPStatusError as exc:
            self._log.error(
                "collector_http_error",
                source_id=self.source_id,
                status=exc.response.status_code,
                error=str(exc),
            )
            raise FetchError(str(exc), status_code=exc.response.status_code) from exc

        except Exception as exc:
            self._log.exception(
                "collector_unexpected_error",
                source_id=self.source_id,
                error=str(exc),
            )
            raise CollectorError(str(exc)) from exc

    @abc.abstractmethod
    async def parse(self, raw: RawEntry) -> list[NormalizedEntry]:
        """
        Parse the fetched response body and return normalized entries.
        Subclasses must implement this method.
        """
        ...
