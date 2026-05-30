"""
Pydantic v2 schemas for the unified data model.
All collectors must produce a NormalizedEntry — this is the contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EntryType(str, Enum):
    CONFIG = "config"
    CREDENTIAL = "credential"
    PROXY = "proxy"
    KEY = "key"
    GENERIC = "generic"


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RawEntry(BaseModel):
    """Raw data exactly as collected from the source."""

    source_id: str
    source_url: str
    raw_content: str
    fetched_at: datetime
    http_status: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedEntry(BaseModel):
    """Unified schema that all collectors must produce after normalization."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    entry_type: EntryType = EntryType.GENERIC
    title: Optional[str] = None
    description: Optional[str] = None
    content: str
    tags: list[str] = Field(default_factory=list)
    fetched_at: datetime
    source_url: str
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content must not be empty")
        return v.strip()

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [t.strip().lower() for t in v.split(",") if t.strip()]
        if isinstance(v, list):
            return [str(t).strip().lower() for t in v if str(t).strip()]
        return []


class ScoringBreakdown(BaseModel):
    """Detailed breakdown of how quality_score was computed."""

    freshness: float = 0.0
    completeness: float = 0.0
    source_reliability: float = 0.0
    validation: float = 0.0
    total: float = 0.0
    notes: list[str] = Field(default_factory=list)


class ProcessedEntry(BaseModel):
    """Final output of the full processing pipeline."""

    raw: RawEntry
    normalized: NormalizedEntry
    content_hash: str
    is_duplicate: bool = False
    validation: ValidationResult = Field(
        default_factory=lambda: ValidationResult(is_valid=True)
    )
    scoring: ScoringBreakdown = Field(default_factory=ScoringBreakdown)

    @property
    def quality_score(self) -> float:
        return self.scoring.total
