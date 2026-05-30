"""
Telegram publishing system.

Features:
- Broadcast to all verified channels
- HTML and MarkdownV2 templates
- Flood control with sleep
- Exponential retry with tenacity
- Per-channel error isolation
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import structlog
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    RetryAfter,
    TelegramError,
    TimedOut,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import get_settings
from src.database.engine import get_db_session
from src.database.models import Entry
from src.database.repositories import (
    ChannelRepository,
    EntryRepository,
    PostRepository,
)

logger = structlog.get_logger(__name__)

# ── Template Renderer ─────────────────────────────────────────────────────


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_md2(text: str) -> str:
    special = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(special)}])", r"\\\1", text)


def render_html(entry_data: dict[str, Any]) -> str:
    title = _escape_html(entry_data.get("title") or "New Entry")
    content = _escape_html(entry_data.get("content", ""))
    source = _escape_html(entry_data.get("source_url", ""))
    score = entry_data.get("quality_score", 0)
    tags = entry_data.get("tags", [])
    tag_str = " ".join(f"#{t}" for t in tags[:5]) if tags else ""

    lines = [f"<b>{title}</b>", "", content]
    if tag_str:
        lines += ["", tag_str]
    lines += [
        "",
        f"📊 Quality: <code>{score:.0f}/100</code>",
        f"🔗 <a href=\"{source}\">Source</a>",
    ]
    return "\n".join(lines)


def render_markdownv2(entry_data: dict[str, Any]) -> str:
    title = _escape_md2(entry_data.get("title") or "New Entry")
    content = _escape_md2(entry_data.get("content", ""))
    source = entry_data.get("source_url", "")
    score = entry_data.get("quality_score", 0)

    return (
        f"*{title}*\n\n{content}\n\n"
        f"📊 Quality: `{score:.0f}/100`\n"
        f"🔗 [Source]({source})"
    )


# ── Publisher ─────────────────────────────────────────────────────────────


class TelegramPublisher:
    """
    Publishes pending entries to all registered, verified channels.

    Flood control: on RetryAfter, we sleep the required amount globally
    (Telegram's flood limits are per-bot, not per-chat).
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._bot = Bot(token=self._settings.telegram.bot_token.get_secret_value())

    async def run_publishing(self) -> dict[str, int]:
        """Fetch all pending posts and publish them. Returns stats."""
        stats = {"published": 0, "failed": 0, "skipped": 0}

        async with get_db_session() as session:
            entry_repo = EntryRepository(session)
            channel_repo = ChannelRepository(session)
            post_repo = PostRepository(session)

            channels = await channel_repo.get_all_active()
            if not channels:
                logger.info("no_active_channels")
                return stats

            min_score = self._settings.scoring.min_publishable_score
            entries = await entry_repo.get_publishable(min_score=min_score, limit=50)

            if not entries:
                logger.info("no_publishable_entries")
                return stats

            for entry in entries:
                for channel in channels:
                    if entry.quality_score < channel.min_quality_score:
                        stats["skipped"] += 1
                        continue

                    rendered = self._render_entry(entry)
                    post = await post_repo.create(
                        entry_id=entry.id,
                        channel_id=channel.id,
                        rendered_text=rendered,
                    )

        # Send outside the session to avoid holding the connection during network I/O
        async with get_db_session() as session:
            post_repo = PostRepository(session)
            channel_repo = ChannelRepository(session)
            pending = await post_repo.get_pending(limit=100)

            for post in pending:
                channel = await channel_repo.get_by_chat_id(
                    await self._get_chat_id_for_post(post)
                )
                if channel is None:
                    await post_repo.mark_failed(post.id, "Channel not found")
                    stats["failed"] += 1
                    continue

                success = await self._send_with_retry(
                    chat_id=channel.chat_id,
                    text=post.rendered_text or "",
                )
                if success:
                    await post_repo.mark_success(post.id, success)
                    await channel_repo.increment_posts(channel.id)
                    stats["published"] += 1
                else:
                    await post_repo.mark_failed(post.id, "Delivery failed after retries")
                    stats["failed"] += 1

        logger.info("publishing_complete", **stats)
        return stats

    async def _get_chat_id_for_post(self, post: Any) -> int:
        # Load the chat_id via join — simplified here for clarity
        async with get_db_session() as session:
            from sqlalchemy import select
            from src.database.models import Channel, Post
            result = await session.execute(
                select(Channel.chat_id)
                .join(Post, Post.channel_id == Channel.id)
                .where(Post.id == post.id)
            )
            return result.scalar_one()

    def _render_entry(self, entry: Entry) -> str:
        data = {
            "title": entry.normalized_data.get("title"),
            "content": entry.normalized_data.get("content", ""),
            "source_url": entry.normalized_data.get("source_url", ""),
            "quality_score": entry.quality_score,
            "tags": entry.normalized_data.get("tags", []),
        }
        if self._settings.telegram.parse_mode == "HTML":
            return render_html(data)
        return render_markdownv2(data)

    async def _send_with_retry(self, chat_id: int, text: str) -> int | None:
        """
        Attempt to send a message. Returns message_id on success, None on failure.
        Handles flood limits gracefully.
        """
        attempts = self._settings.telegram.max_retries
        for attempt in range(1, attempts + 1):
            try:
                msg = await self._bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=(
                        ParseMode.HTML
                        if self._settings.telegram.parse_mode == "HTML"
                        else ParseMode.MARKDOWN_V2
                    ),
                    disable_web_page_preview=True,
                )
                return msg.message_id

            except RetryAfter as exc:
                sleep_for = exc.retry_after + 1
                logger.warning(
                    "flood_control_triggered",
                    chat_id=chat_id,
                    sleep=sleep_for,
                    attempt=attempt,
                )
                await asyncio.sleep(sleep_for)

            except (TimedOut, NetworkError) as exc:
                wait = 2 ** attempt
                logger.warning(
                    "telegram_network_error",
                    chat_id=chat_id,
                    error=str(exc),
                    retry_in=wait,
                )
                await asyncio.sleep(wait)

            except Forbidden:
                logger.error("bot_kicked_from_channel", chat_id=chat_id)
                return None

            except BadRequest as exc:
                logger.error("bad_request", chat_id=chat_id, error=str(exc))
                return None

            except TelegramError as exc:
                logger.error("telegram_error", chat_id=chat_id, error=str(exc))
                if attempt == attempts:
                    return None
                await asyncio.sleep(2 ** attempt)

        return None

    async def send_admin_alert(self, message: str) -> None:
        """Send a plain alert message to the configured admin chat."""
        admin_id = self._settings.telegram.admin_chat_id
        try:
            await self._bot.send_message(
                chat_id=admin_id,
                text=f"🚨 <b>Bot Alert</b>\n\n{_escape_html(message)}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as exc:
            logger.error("admin_alert_failed", error=str(exc))

    async def verify_admin_in_channel(self, chat_id: int) -> bool:
        """Check that the bot has admin privileges in the given channel."""
        try:
            me = await self._bot.get_me()
            member = await self._bot.get_chat_member(chat_id, me.id)
            return member.status in ("administrator", "creator")
        except TelegramError as exc:
            logger.warning("admin_check_failed", chat_id=chat_id, error=str(exc))
            return False
