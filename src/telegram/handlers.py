"""
Telegram bot command handlers.
Admin commands for channel registration, status, and management.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import structlog

from src.config import get_settings
from src.database.engine import get_db_session
from src.database.repositories import ChannelRepository, EntryRepository, SourceRepository
from src.telegram.publisher import TelegramPublisher

logger = structlog.get_logger(__name__)
settings = get_settings()


def is_admin(user_id: int) -> bool:
    return user_id == settings.telegram.admin_chat_id


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "👋 <b>TGBot Collector</b>\n\n"
        "Available commands:\n"
        "/register — Register this channel\n"
        "/status — Show bot status\n"
        "/stats — Show entry statistics\n"
        "/sources — List active sources\n"
        "/help — Show this message",
        parse_mode="HTML",
    )


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    chat = update.effective_chat
    publisher = TelegramPublisher()

    is_admin_in_channel = await publisher.verify_admin_in_channel(chat.id)
    if not is_admin_in_channel:
        await update.message.reply_text(
            "❌ I need to be an <b>administrator</b> in this channel to register it.",
            parse_mode="HTML",
        )
        return

    async with get_db_session() as session:
        repo = ChannelRepository(session)
        channel = await repo.register(
            chat_id=chat.id,
            title=chat.title or str(chat.id),
            username=chat.username,
        )
        await repo.verify_admin(chat.id)

    await update.message.reply_text(
        f"✅ Channel <b>{chat.title}</b> registered and verified!\n"
        f"Posts will appear here shortly.",
        parse_mode="HTML",
    )
    logger.info("channel_registered", chat_id=chat.id, title=chat.title)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    from src.database.engine import check_db_connection

    db_ok = await check_db_connection()

    async with get_db_session() as session:
        entry_repo = EntryRepository(session)
        stats = await entry_repo.get_stats()

    text = (
        "📊 <b>Bot Status</b>\n\n"
        f"🗄 Database: {'✅ OK' if db_ok else '❌ DOWN'}\n"
        f"📥 Entries total: <code>{stats['total']}</code>\n"
        f"✅ Valid entries: <code>{stats['valid']}</code>\n"
        f"📤 Published: <code>{stats['published']}</code>\n"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    async with get_db_session() as session:
        entry_repo = EntryRepository(session)
        stats = await entry_repo.get_stats()

    await update.message.reply_text(
        f"📈 <b>Entry Statistics</b>\n\n"
        f"Total: <code>{stats['total']}</code>\n"
        f"Valid: <code>{stats['valid']}</code>\n"
        f"Published: <code>{stats['published']}</code>",
        parse_mode="HTML",
    )


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    async with get_db_session() as session:
        src_repo = SourceRepository(session)
        sources = await src_repo.get_all_active()

    if not sources:
        await update.message.reply_text("No active sources configured.")
        return

    lines = ["📡 <b>Active Sources</b>\n"]
    for src in sources:
        status_icon = "🟢" if src.consecutive_failures == 0 else "🔴"
        lines.append(
            f"{status_icon} <b>{src.source_name}</b>\n"
            f"   Reliability: {src.reliability_score:.2f}\n"
            f"   Failures: {src.consecutive_failures}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def build_application() -> Application:
    """Build and configure the telegram.ext.Application."""
    app = (
        Application.builder()
        .token(settings.telegram.bot_token.get_secret_value())
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("sources", cmd_sources))
    return app
