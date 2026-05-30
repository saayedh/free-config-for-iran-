"""
Health checks and monitoring alerts.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.config import get_settings
from src.database.engine import check_db_connection, get_db_session
from src.database.repositories import FetchLogRepository, SourceRepository

logger = structlog.get_logger(__name__)


async def run_health_checks() -> dict[str, Any]:
    """Run all health checks. Returns a dict of check → status."""
    results: dict[str, Any] = {}
    alerts: list[str] = []
    settings = get_settings()

    # 1. Database connectivity
    db_ok = await check_db_connection()
    results["database"] = "ok" if db_ok else "error"
    if not db_ok:
        alerts.append("⚠️ Database connection failed")

    # 2. Source failure monitoring
    if db_ok:
        async with get_db_session() as session:
            src_repo = SourceRepository(session)
            log_repo = FetchLogRepository(session)
            sources = await src_repo.get_all_active()

            for source in sources:
                failures = await log_repo.get_failure_count(source.id, since_hours=24)
                threshold = settings.monitoring.alert_on_consecutive_failures
                results[f"source:{source.source_id}"] = f"failures:{failures}"
                if failures >= threshold:
                    alerts.append(
                        f"🔴 Source <b>{source.source_name}</b> failed "
                        f"{failures} times in last 24h"
                    )

    # 3. Send alerts
    if alerts:
        from src.telegram.publisher import TelegramPublisher
        publisher = TelegramPublisher()
        combined = "\n".join(alerts)
        await publisher.send_admin_alert(f"Health Check Alerts:\n\n{combined}")
        logger.warning("health_alerts_sent", count=len(alerts))

    return results


async def run_cleanup() -> dict[str, int]:
    """Prune old logs and published entries beyond retention window."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete
    from src.database.models import FetchLog, SystemLog

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
    deleted = 0

    async with get_db_session() as session:
        result = await session.execute(
            delete(FetchLog).where(FetchLog.started_at < cutoff)
        )
        deleted += result.rowcount  # type: ignore[attr-defined]

        result2 = await session.execute(
            delete(SystemLog).where(SystemLog.timestamp < cutoff)
        )
        deleted += result2.rowcount  # type: ignore[attr-defined]

    logger.info("cleanup_complete", rows_deleted=deleted)
    return {"rows_deleted": deleted}
