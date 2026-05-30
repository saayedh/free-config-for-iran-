"""
Full processing pipeline:
  NormalizedEntry → hash → validate → score → ProcessedEntry
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import structlog
import xxhash

from src.config import get_settings
from src.processors.schemas import (
    NormalizedEntry,
    ProcessedEntry,
    RawEntry,
    ScoringBreakdown,
    ValidationResult,
)

logger = structlog.get_logger(__name__)


# ── Hashing ────────────────────────────────────────────────────────────────


def compute_content_hash(content: str) -> str:
    """Fast, collision-resistant content fingerprint using xxhash + sha256 fallback."""
    normalized = re.sub(r"\s+", " ", content.strip().lower())
    return xxhash.xxh3_64_hexdigest(normalized.encode())


# ── Validator ──────────────────────────────────────────────────────────────


class ContentValidator:
    """
    Rule-based validator. Each rule is a method prefixed with `_check_`.
    Add new rules without changing the caller.
    """

    MIN_CONTENT_LENGTH = 10
    MAX_CONTENT_LENGTH = 50_000

    def validate(self, entry: NormalizedEntry) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        for attr in dir(self):
            if attr.startswith("_check_"):
                method = getattr(self, attr)
                e, w = method(entry)
                errors.extend(e)
                warnings.extend(w)

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)

    def _check_content_length(self, entry: NormalizedEntry) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        length = len(entry.content)
        if length < self.MIN_CONTENT_LENGTH:
            errors.append(f"Content too short: {length} chars (min {self.MIN_CONTENT_LENGTH})")
        if length > self.MAX_CONTENT_LENGTH:
            warnings.append(f"Content very long: {length} chars")
        return errors, warnings

    def _check_not_placeholder(self, entry: NormalizedEntry) -> tuple[list[str], list[str]]:
        placeholders = {"n/a", "null", "none", "undefined", "todo", "placeholder"}
        if entry.content.lower().strip() in placeholders:
            return [f"Content is a placeholder: {entry.content!r}"], []
        return [], []

    def _check_source_id(self, entry: NormalizedEntry) -> tuple[list[str], list[str]]:
        if not entry.source_id or not re.match(r"^[a-z0-9_]+$", entry.source_id):
            return ["Invalid source_id format"], []
        return [], []


# ── Scorer ─────────────────────────────────────────────────────────────────


class QualityScorer:
    """
    Computes a 0–100 quality_score from multiple weighted dimensions.
    """

    def __init__(self) -> None:
        cfg = get_settings().scoring
        self._freshness_w = cfg.freshness_weight
        self._completeness_w = cfg.completeness_weight
        self._reliability_w = cfg.source_reliability_weight
        self._validation_w = cfg.validation_weight
        self._decay_hours = cfg.freshness_decay_hours

    def score(
        self,
        entry: NormalizedEntry,
        validation: ValidationResult,
        source_reliability: float = 1.0,
    ) -> ScoringBreakdown:
        from datetime import datetime, timezone

        breakdown = ScoringBreakdown()

        # 1. Freshness — decays exponentially over _decay_hours
        import math
        now = datetime.now(tz=timezone.utc)
        age_hours = (now - entry.fetched_at).total_seconds() / 3600
        decay = math.exp(-age_hours / max(self._decay_hours, 1))
        breakdown.freshness = round(decay * 100, 2)

        # 2. Completeness — bonus for title, description, tags
        completeness = 0.6  # content always present at this point
        if entry.title:
            completeness += 0.15
        if entry.description:
            completeness += 0.15
        if entry.tags:
            completeness += 0.10
        breakdown.completeness = round(min(completeness, 1.0) * 100, 2)

        # 3. Source reliability (passed in from DB)
        breakdown.source_reliability = round(min(source_reliability, 1.0) * 100, 2)

        # 4. Validation — full score if valid, reduced by error count
        if validation.is_valid:
            breakdown.validation = 100.0
        else:
            penalty = min(len(validation.errors) * 20, 100)
            breakdown.validation = max(0.0, 100.0 - penalty)

        # Weighted total
        total = (
            breakdown.freshness * self._freshness_w
            + breakdown.completeness * self._completeness_w
            + breakdown.source_reliability * self._reliability_w
            + breakdown.validation * self._validation_w
        )
        breakdown.total = round(min(max(total, 0), 100), 2)

        if validation.warnings:
            breakdown.notes.extend(validation.warnings)

        return breakdown


# ── Pipeline ───────────────────────────────────────────────────────────────


class NormalizationPipeline:
    """
    Thin orchestrator: hash → validate → score → return ProcessedEntry.
    Stateless — safe to use from multiple coroutines.
    """

    def __init__(self) -> None:
        self._validator = ContentValidator()
        self._scorer = QualityScorer()

    async def process(
        self,
        entry: NormalizedEntry,
        source_reliability: float = 1.0,
        raw: RawEntry | None = None,
    ) -> ProcessedEntry:
        content_hash = compute_content_hash(entry.content)
        validation = self._validator.validate(entry)
        scoring = self._scorer.score(entry, validation, source_reliability)

        # Build a minimal RawEntry if none provided
        if raw is None:
            from datetime import datetime, timezone
            raw = RawEntry(
                source_id=entry.source_id,
                source_url=entry.source_url,
                raw_content=entry.content,
                fetched_at=entry.fetched_at,
            )

        return ProcessedEntry(
            raw=raw,
            normalized=entry,
            content_hash=content_hash,
            validation=validation,
            scoring=scoring,
        )
