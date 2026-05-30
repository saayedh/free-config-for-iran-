"""
Repository layer — all database access goes through these classes.
Business logic is never mixed with SQL queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Channel,
    Entry,
    FetchLog,
    Job,
    JobStatus,
    Post,
    PostStatus,
    Setting,
    Source,
    SourceStatus,
    SystemLog,
)

logger = structlog.get_logger(__name__)


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all_active(self) -> list[Source]:
        result = await self.session.execute(
            select(Source).where(Source.status == SourceStatus.ACTIVE)
        )
        return list(result.scalars().all())

    async def get_by_source_id(self, source_id: str) -> Optional[Source]:
        result = await self.session.execute(
            select(Source).where(Source.source_id == source_id)
        )
        return result.scalar_one_or_none()

    async def upsert(self, source_id: str, **kwargs: object) -> Source:
        existing = await self.get_by_source_id(source_id)
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            return existing
        source = Source(source_id=source_id, **kwargs)
        self.session.add(source)
        await self.session.flush()
        return source

    async def increment_failure(self, source_id: str) -> None:
        await self.session.execute(
            update(Source)
            .where(Source.source_id == source_id)
            .values(consecutive_failures=Source.consecutive_failures + 1)
        )

    async def reset_failures(self, source_id: str, fetched_at: datetime) -> None:
        await self.session.execute(
            update(Source)
            .where(Source.source_id == source_id)
            .values(consecutive_failures=0, last_successful_fetch=fetched_at)
        )

    async def update_reliability(self, source_id: str, score: float) -> None:
        await self.session.execute(
            update(Source)
            .where(Source.source_id == source_id)
            .values(reliability_score=score)
        )


class EntryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def exists_by_hash(self, content_hash: str) -> bool:
        result = await self.session.execute(
            select(func.count()).where(Entry.content_hash == content_hash)
        )
        return (result.scalar() or 0) > 0

    async def create(self, **kwargs: object) -> Entry:
        entry = Entry(**kwargs)
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get_publishable(
        self,
        min_score: float,
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> list[Entry]:
        filters = [
            Entry.is_valid == True,  # noqa: E712
            Entry.is_duplicate == False,  # noqa: E712
            Entry.quality_score >= min_score,
            Entry.published_at == None,  # noqa: E711
        ]
        if since:
            filters.append(Entry.fetched_at >= since)
        result = await self.session.execute(
            select(Entry)
            .where(and_(*filters))
            .order_by(Entry.quality_score.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_published(self, entry_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Entry)
            .where(Entry.id == entry_id)
            .values(published_at=datetime.now(tz=timezone.utc))
        )

    async def get_stats(self) -> dict[str, int]:
        total = await self.session.scalar(select(func.count(Entry.id))) or 0
        valid = await self.session.scalar(
            select(func.count(Entry.id)).where(Entry.is_valid == True)  # noqa: E712
        ) or 0
        published = await self.session.scalar(
            select(func.count(Entry.id)).where(Entry.published_at != None)  # noqa: E711
        ) or 0
        return {"total": total, "valid": valid, "published": published}


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all_active(self) -> list[Channel]:
        result = await self.session.execute(
            select(Channel).where(
                and_(Channel.is_active == True, Channel.is_admin_verified == True)  # noqa: E712
            )
        )
        return list(result.scalars().all())

    async def get_by_chat_id(self, chat_id: int) -> Optional[Channel]:
        result = await self.session.execute(
            select(Channel).where(Channel.chat_id == chat_id)
        )
        return result.scalar_one_or_none()

    async def register(self, chat_id: int, title: str, username: Optional[str] = None) -> Channel:
        existing = await self.get_by_chat_id(chat_id)
        if existing:
            existing.title = title
            existing.username = username
            return existing
        channel = Channel(chat_id=chat_id, title=title, username=username)
        self.session.add(channel)
        await self.session.flush()
        return channel

    async def verify_admin(self, chat_id: int) -> None:
        await self.session.execute(
            update(Channel)
            .where(Channel.chat_id == chat_id)
            .values(is_admin_verified=True)
        )

    async def increment_posts(self, channel_id: uuid.UUID) -> None:
        from datetime import datetime, timezone
        await self.session.execute(
            update(Channel)
            .where(Channel.id == channel_id)
            .values(
                posts_sent=Channel.posts_sent + 1,
                last_post_at=datetime.now(tz=timezone.utc),
            )
        )


class PostRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, entry_id: uuid.UUID, channel_id: uuid.UUID, rendered_text: str) -> Post:
        post = Post(
            entry_id=entry_id,
            channel_id=channel_id,
            rendered_text=rendered_text,
        )
        self.session.add(post)
        await self.session.flush()
        return post

    async def get_pending(self, limit: int = 100) -> list[Post]:
        result = await self.session.execute(
            select(Post)
            .where(
                and_(
                    Post.status == PostStatus.PENDING,
                    Post.attempt_count < 5,
                )
            )
            .order_by(Post.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_success(self, post_id: uuid.UUID, message_id: int) -> None:
        from datetime import datetime, timezone
        await self.session.execute(
            update(Post)
            .where(Post.id == post_id)
            .values(
                status=PostStatus.PUBLISHED,
                message_id=message_id,
                published_at=datetime.now(tz=timezone.utc),
                attempt_count=Post.attempt_count + 1,
            )
        )

    async def mark_failed(self, post_id: uuid.UUID, error: str) -> None:
        await self.session.execute(
            update(Post)
            .where(Post.id == post_id)
            .values(
                status=PostStatus.FAILED,
                last_error=error,
                attempt_count=Post.attempt_count + 1,
            )
        )

    async def increment_attempt(self, post_id: uuid.UUID, error: str) -> None:
        await self.session.execute(
            update(Post)
            .where(Post.id == post_id)
            .values(attempt_count=Post.attempt_count + 1, last_error=error)
        )


class FetchLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, source_id: uuid.UUID, started_at: datetime) -> FetchLog:
        log = FetchLog(source_id=source_id, started_at=started_at, success=False)
        self.session.add(log)
        await self.session.flush()
        return log

    async def complete(
        self,
        log_id: uuid.UUID,
        success: bool,
        items_fetched: int,
        items_new: int,
        items_duplicate: int,
        items_invalid: int,
        error_message: Optional[str] = None,
        http_status: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        from datetime import datetime, timezone
        await self.session.execute(
            update(FetchLog)
            .where(FetchLog.id == log_id)
            .values(
                finished_at=datetime.now(tz=timezone.utc),
                success=success,
                items_fetched=items_fetched,
                items_new=items_new,
                items_duplicate=items_duplicate,
                items_invalid=items_invalid,
                error_message=error_message,
                http_status=http_status,
                duration_ms=duration_ms,
            )
        )

    async def get_failure_count(self, source_id: uuid.UUID, since_hours: int = 24) -> int:
        since = datetime.now(tz=timezone.utc) - timedelta(hours=since_hours)
        result = await self.session.scalar(
            select(func.count(FetchLog.id)).where(
                and_(
                    FetchLog.source_id == source_id,
                    FetchLog.success == False,  # noqa: E712
                    FetchLog.started_at >= since,
                )
            )
        )
        return result or 0


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(self, job_name: str, job_type: str) -> Job:
        from datetime import datetime, timezone
        job = Job(
            job_name=job_name,
            job_type=job_type,
            status=JobStatus.RUNNING,
            started_at=datetime.now(tz=timezone.utc),
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def finish(
        self,
        job_id: uuid.UUID,
        status: JobStatus,
        result: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc)
        job = await self.session.get(Job, job_id)
        if job:
            duration_ms = int((now - job.started_at).total_seconds() * 1000)
            await self.session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status=status,
                    finished_at=now,
                    duration_ms=duration_ms,
                    result=result,
                    error=error,
                )
            )


class SettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> Optional[str]:
        result = await self.session.execute(
            select(Setting).where(Setting.key == key)
        )
        setting = result.scalar_one_or_none()
        return setting.value if setting else None

    async def set(self, key: str, value: str, description: Optional[str] = None) -> None:
        result = await self.session.execute(
            select(Setting).where(Setting.key == key)
        )
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            self.session.add(Setting(key=key, value=value, description=description))
