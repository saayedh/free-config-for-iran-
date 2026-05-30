"""
SQLAlchemy 2.0 ORM models — full PostgreSQL schema.
Uses declarative base with type annotations (mapped_column).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ─────────────────────────── Enums ────────────────────────────


class SourceStatus(str, PyEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    DISABLED = "disabled"


class PostStatus(str, PyEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobStatus(str, PyEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class LogLevel(str, PyEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ─────────────────────────── Base ─────────────────────────────


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ─────────────────────────── Models ───────────────────────────


class Source(Base, TimestampMixin):
    """Represents a data source that the collector polls."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    collector_class: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[SourceStatus] = mapped_column(
        String(20), default=SourceStatus.ACTIVE, nullable=False
    )
    fetch_interval: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    reliability_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_successful_fetch: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    fetch_logs: Mapped[list[FetchLog]] = relationship(back_populates="source")
    entries: Mapped[list[Entry]] = relationship(back_populates="source")

    __table_args__ = (
        Index("ix_sources_status", "status"),
        Index("ix_sources_source_id", "source_id"),
    )


class Entry(Base, TimestampMixin):
    """A single normalized data entry collected from a source."""

    __tablename__ = "entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    normalized_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation_errors: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    source: Mapped[Source] = relationship(back_populates="entries")
    posts: Mapped[list[Post]] = relationship(back_populates="entry")

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_entries_content_hash"),
        Index("ix_entries_source_id", "source_id"),
        Index("ix_entries_quality_score", "quality_score"),
        Index("ix_entries_is_duplicate", "is_duplicate"),
        Index("ix_entries_is_valid", "is_valid"),
        Index("ix_entries_fetched_at", "fetched_at"),
    )


class Channel(Base, TimestampMixin):
    """A Telegram channel the bot publishes to."""

    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    min_quality_score: Mapped[float] = mapped_column(Float, default=40.0, nullable=False)
    posts_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_post_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    posts: Mapped[list[Post]] = relationship(back_populates="channel")

    __table_args__ = (
        Index("ix_channels_chat_id", "chat_id"),
        Index("ix_channels_is_active", "is_active"),
    )


class Post(Base, TimestampMixin):
    """Tracks each publishing attempt to a channel."""

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[PostStatus] = mapped_column(
        String(20), default=PostStatus.PENDING, nullable=False
    )
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    rendered_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    entry: Mapped[Entry] = relationship(back_populates="posts")
    channel: Mapped[Channel] = relationship(back_populates="posts")

    __table_args__ = (
        Index("ix_posts_status", "status"),
        Index("ix_posts_entry_id", "entry_id"),
        Index("ix_posts_channel_id", "channel_id"),
        Index("ix_posts_published_at", "published_at"),
    )


class FetchLog(Base):
    """Immutable audit log for each collection run per source."""

    __tablename__ = "fetch_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    items_fetched: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    items_invalid: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    source: Mapped[Source] = relationship(back_populates="fetch_logs")

    __table_args__ = (
        Index("ix_fetch_logs_source_id", "source_id"),
        Index("ix_fetch_logs_started_at", "started_at"),
        Index("ix_fetch_logs_success", "success"),
    )


class SystemLog(Base):
    """Structured system-wide log entries persisted for audit."""

    __tablename__ = "system_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    level: Mapped[LogLevel] = mapped_column(String(20), nullable=False)
    logger: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_system_logs_level", "level"),
        Index("ix_system_logs_timestamp", "timestamp"),
        Index("ix_system_logs_logger", "logger"),
    )


class Job(Base, TimestampMixin):
    """Tracks scheduled job executions."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_name: Mapped[str] = mapped_column(String(100), nullable=False)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        String(20), default=JobStatus.RUNNING, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_jobs_job_name", "job_name"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_started_at", "started_at"),
    )


class Setting(Base, TimestampMixin):
    """Key-value runtime settings stored in database."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_settings_key", "key"),)
