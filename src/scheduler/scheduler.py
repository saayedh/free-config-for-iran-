"""
Scheduler — wraps APScheduler with structured logging and job tracking.
All jobs are registered here. Each job is wrapped in an error-safe runner.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import get_settings
from src.database.engine import get_db_session
from src.database.models import JobStatus
from src.database.repositories import JobRepository

logger = structlog.get_logger(__name__)


async def _run_job(
    name: str,
    job_type: str,
    coro_factory: Callable[[], Coroutine[Any, Any, dict[str, Any] | None]],
) -> None:
    """
    Wrapper that persists job start/end to the database and catches all errors.
    Never raises — scheduler must never crash.
    """
    async with get_db_session() as session:
        job_repo = JobRepository(session)
        job = await job_repo.start(name, job_type)
        job_id = job.id

    try:
        logger.info("job_started", name=name)
        result = await coro_factory()
        async with get_db_session() as session:
            job_repo = JobRepository(session)
            await job_repo.finish(job_id, JobStatus.SUCCESS, result=result or {})
        logger.info("job_finished", name=name, result=result)

    except Exception as exc:
        logger.exception("job_failed", name=name, error=str(exc))
        async with get_db_session() as session:
            job_repo = JobRepository(session)
            await job_repo.finish(job_id, JobStatus.FAILED, error=str(exc))

        # Alert admin on job failure
        try:
            from src.telegram.publisher import TelegramPublisher
            publisher = TelegramPublisher()
            await publisher.send_admin_alert(f"Job <b>{name}</b> failed:\n{exc}")
        except Exception:
            pass


def build_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler with all jobs."""
    cfg = get_settings().scheduler
    scheduler = AsyncIOScheduler(timezone=cfg.timezone)

    # ── Collection job ─────────────────────────────────────────
    async def run_collection() -> dict[str, Any]:
        from src.collectors.orchestrator import CollectionOrchestrator
        orch = CollectionOrchestrator()
        return await orch.run_collection()

    scheduler.add_job(
        lambda: asyncio.create_task(
            _run_job("collection", "scheduled", run_collection)
        ),
        trigger=CronTrigger.from_crontab(cfg.collection_cron, timezone=cfg.timezone),
        id="collection",
        name="Data Collection",
        replace_existing=True,
        max_instances=1,
    )

    # ── Publishing job ─────────────────────────────────────────
    async def run_publishing() -> dict[str, Any]:
        from src.telegram.publisher import TelegramPublisher
        publisher = TelegramPublisher()
        return await publisher.run_publishing()

    scheduler.add_job(
        lambda: asyncio.create_task(
            _run_job("publishing", "scheduled", run_publishing)
        ),
        trigger=CronTrigger.from_crontab(cfg.publishing_cron, timezone=cfg.timezone),
        id="publishing",
        name="Telegram Publishing",
        replace_existing=True,
        max_instances=1,
    )

    # ── Health check job ───────────────────────────────────────
    async def run_healthcheck() -> dict[str, Any]:
        from src.monitoring.health import run_health_checks
        return await run_health_checks()

    scheduler.add_job(
        lambda: asyncio.create_task(
            _run_job("healthcheck", "scheduled", run_healthcheck)
        ),
        trigger=CronTrigger.from_crontab(cfg.healthcheck_cron, timezone=cfg.timezone),
        id="healthcheck",
        name="Health Check",
        replace_existing=True,
        max_instances=1,
    )

    # ── Cleanup job ────────────────────────────────────────────
    async def run_cleanup() -> dict[str, Any]:
        from src.monitoring.cleanup import run_cleanup
        return await run_cleanup()

    scheduler.add_job(
        lambda: asyncio.create_task(
            _run_job("cleanup", "maintenance", run_cleanup)
        ),
        trigger=CronTrigger.from_crontab(cfg.cleanup_cron, timezone=cfg.timezone),
        id="cleanup",
        name="Database Cleanup",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler
