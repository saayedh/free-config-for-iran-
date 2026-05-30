"""
CollectionOrchestrator — runs all active collectors concurrently,
isolates failures, persists results, and updates source health.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from src.collectors.base import BaseCollector, CollectorError
from src.collectors.implementations import COLLECTOR_REGISTRY, get_collector
from src.config import get_settings
from src.database.engine import get_db_session
from src.database.repositories import (
    EntryRepository,
    FetchLogRepository,
    SourceRepository,
)
from src.processors.normalizer import NormalizationPipeline
from src.processors.schemas import NormalizedEntry, ProcessedEntry

logger = structlog.get_logger(__name__)


class CollectionOrchestrator:
    """
    Coordinates all collector plugins.

    Failure isolation: each collector runs in its own asyncio Task with
    independent error handling. One broken source never blocks others.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._pipeline = NormalizationPipeline()
        self._semaphore = asyncio.Semaphore(self._settings.collector.max_concurrent)

    # ── Public entry point ─────────────────────────────────────

    async def run_collection(self) -> dict[str, Any]:
        """
        Fetch all active sources concurrently.
        Returns a summary dict suitable for job result storage.
        """
        async with get_db_session() as session:
            source_repo = SourceRepository(session)
            sources = await source_repo.get_all_active()

        if not sources:
            logger.info("no_active_sources")
            return {"sources": 0, "total_new": 0}

        tasks = [
            asyncio.create_task(
                self._collect_one(src.source_id, src.source_url, src.id),
                name=f"collect:{src.source_id}",
            )
            for src in sources
            if src.source_id in COLLECTOR_REGISTRY
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        total_new = sum(
            r.get("new", 0) for r in results if isinstance(r, dict)
        )
        errors = sum(1 for r in results if isinstance(r, Exception))

        summary = {
            "sources": len(tasks),
            "total_new": total_new,
            "errors": errors,
        }
        logger.info("collection_complete", **summary)
        return summary

    # ── Per-source collection ──────────────────────────────────

    async def _collect_one(
        self,
        source_id: str,
        source_url: str,
        db_source_id: Any,
    ) -> dict[str, Any]:
        async with self._semaphore:
            started_at = datetime.now(tz=timezone.utc)
            fetch_log_id = None

            async with get_db_session() as session:
                log_repo = FetchLogRepository(session)
                log = await log_repo.create(db_source_id, started_at)
                fetch_log_id = log.id

            try:
                collector = get_collector(source_id)
                entries = await collector.collect()

                stats = await self._process_entries(entries, source_id, db_source_id)

                async with get_db_session() as session:
                    log_repo = FetchLogRepository(session)
                    src_repo = SourceRepository(session)
                    duration_ms = int(
                        (datetime.now(tz=timezone.utc) - started_at).total_seconds() * 1000
                    )
                    await log_repo.complete(
                        fetch_log_id,
                        success=True,
                        items_fetched=stats["fetched"],
                        items_new=stats["new"],
                        items_duplicate=stats["duplicate"],
                        items_invalid=stats["invalid"],
                        duration_ms=duration_ms,
                    )
                    await src_repo.reset_failures(source_id, started_at)

                return stats

            except CollectorError as exc:
                logger.error("source_collection_failed", source_id=source_id, error=str(exc))
                await self._record_failure(fetch_log_id, db_source_id, source_id, str(exc))
                return {"fetched": 0, "new": 0, "duplicate": 0, "invalid": 0, "error": str(exc)}

    async def _process_entries(
        self,
        entries: list[NormalizedEntry],
        source_id: str,
        db_source_id: Any,
    ) -> dict[str, int]:
        stats = {"fetched": len(entries), "new": 0, "duplicate": 0, "invalid": 0}

        for normalized in entries:
            processed: ProcessedEntry = await self._pipeline.process(normalized)

            async with get_db_session() as session:
                entry_repo = EntryRepository(session)

                if await entry_repo.exists_by_hash(processed.content_hash):
                    stats["duplicate"] += 1
                    continue

                if not processed.validation.is_valid:
                    stats["invalid"] += 1
                    continue

                await entry_repo.create(
                    source_id=db_source_id,
                    content_hash=processed.content_hash,
                    raw_data=processed.raw.model_dump(mode="json"),
                    normalized_data=processed.normalized.model_dump(mode="json"),
                    quality_score=processed.quality_score,
                    is_duplicate=processed.is_duplicate,
                    is_valid=processed.validation.is_valid,
                    validation_errors=processed.validation.errors or None,
                    fetched_at=normalized.fetched_at,
                )
                stats["new"] += 1

        return stats

    async def _record_failure(
        self,
        fetch_log_id: Any,
        db_source_id: Any,
        source_id: str,
        error: str,
    ) -> None:
        async with get_db_session() as session:
            log_repo = FetchLogRepository(session)
            src_repo = SourceRepository(session)
            if fetch_log_id:
                await log_repo.complete(
                    fetch_log_id,
                    success=False,
                    items_fetched=0,
                    items_new=0,
                    items_duplicate=0,
                    items_invalid=0,
                    error_message=error,
                )
            await src_repo.increment_failure(source_id)
