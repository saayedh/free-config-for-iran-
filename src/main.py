"""
Application entrypoint.

Modes:
  bot      — run the Telegram bot + scheduler (default)
  collect  — one-shot collection run (for GitHub Actions)
  publish  — one-shot publishing run (for GitHub Actions)
  migrate  — run Alembic migrations
  health   — run health checks and exit
"""

from __future__ import annotations

import asyncio
import sys

import structlog

logger = structlog.get_logger(__name__)


async def run_bot() -> None:
    """Start the bot, scheduler, and polling loop."""
    from src.config import get_settings
    from src.scheduler.scheduler import build_scheduler
    from src.telegram.handlers import build_application
    from src.utils.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    logger.info("bot_starting", version=settings.version, env=settings.environment)

    scheduler = build_scheduler()
    scheduler.start()
    logger.info("scheduler_started")

    app = build_application()
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]
        logger.info("bot_polling_started")

        # Run until interrupted
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            logger.info("bot_shutting_down")
            scheduler.shutdown(wait=False)
            await app.updater.stop()  # type: ignore[union-attr]
            await app.stop()

    from src.database.engine import close_engine
    await close_engine()
    logger.info("bot_stopped")


async def run_collect() -> None:
    from src.collectors.orchestrator import CollectionOrchestrator
    from src.config import get_settings
    from src.utils.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    logger.info("one_shot_collection_start")
    orch = CollectionOrchestrator()
    result = await orch.run_collection()
    logger.info("one_shot_collection_done", **result)


async def run_publish() -> None:
    from src.config import get_settings
    from src.telegram.publisher import TelegramPublisher
    from src.utils.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    logger.info("one_shot_publishing_start")
    publisher = TelegramPublisher()
    result = await publisher.run_publishing()
    logger.info("one_shot_publishing_done", **result)


async def run_health() -> None:
    from src.config import get_settings
    from src.monitoring.health import run_health_checks
    from src.utils.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    result = await run_health_checks()
    ok = all(v == "ok" or "failures:0" in str(v) for v in result.values())
    logger.info("health_check_complete", ok=ok, details=result)
    sys.exit(0 if ok else 1)


def cli() -> None:
    commands = {
        "bot": run_bot,
        "collect": run_collect,
        "publish": run_publish,
        "health": run_health,
    }
    cmd = sys.argv[1] if len(sys.argv) > 1 else "bot"
    if cmd not in commands:
        print(f"Unknown command: {cmd}. Valid: {list(commands)}")
        sys.exit(1)
    asyncio.run(commands[cmd]())


if __name__ == "__main__":
    cli()
