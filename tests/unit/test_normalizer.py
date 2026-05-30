"""
Unit tests for the normalization pipeline, validator, and scorer.
No database or network required.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from src.processors.normalizer import (
    ContentValidator,
    NormalizationPipeline,
    QualityScorer,
    compute_content_hash,
)
from src.processors.schemas import EntryType, NormalizedEntry, RawEntry


# ── Fixtures ───────────────────────────────────────────────────────────────


def make_entry(**kwargs) -> NormalizedEntry:
    defaults = dict(
        source_id="test_source",
        content="This is a valid test entry with sufficient content.",
        fetched_at=datetime.now(tz=timezone.utc),
        source_url="https://example.com/test",
    )
    defaults.update(kwargs)
    return NormalizedEntry(**defaults)


# ── Hashing ────────────────────────────────────────────────────────────────


def test_hash_is_deterministic():
    content = "hello world"
    assert compute_content_hash(content) == compute_content_hash(content)


def test_hash_is_case_insensitive():
    assert compute_content_hash("Hello World") == compute_content_hash("hello world")


def test_hash_whitespace_normalized():
    assert compute_content_hash("a  b  c") == compute_content_hash("a b c")


def test_different_content_different_hash():
    assert compute_content_hash("abc") != compute_content_hash("xyz")


# ── Validator ──────────────────────────────────────────────────────────────


def test_valid_entry_passes():
    validator = ContentValidator()
    entry = make_entry()
    result = validator.validate(entry)
    assert result.is_valid
    assert result.errors == []


def test_too_short_content_fails():
    validator = ContentValidator()
    entry = make_entry(content="hi")
    result = validator.validate(entry)
    assert not result.is_valid
    assert any("short" in e.lower() for e in result.errors)


def test_placeholder_content_fails():
    validator = ContentValidator()
    entry = make_entry(content="null")
    result = validator.validate(entry)
    assert not result.is_valid


def test_invalid_source_id_fails():
    validator = ContentValidator()
    entry = make_entry(source_id="INVALID SOURCE ID!")
    result = validator.validate(entry)
    assert not result.is_valid
    assert any("source_id" in e.lower() for e in result.errors)


# ── Scorer ─────────────────────────────────────────────────────────────────


def test_score_is_between_0_and_100():
    scorer = QualityScorer()
    from src.processors.schemas import ValidationResult
    entry = make_entry()
    validation = ValidationResult(is_valid=True)
    breakdown = scorer.score(entry, validation)
    assert 0 <= breakdown.total <= 100


def test_invalid_entry_lower_score():
    scorer = QualityScorer()
    from src.processors.schemas import ValidationResult
    entry = make_entry()
    valid_result = ValidationResult(is_valid=True)
    invalid_result = ValidationResult(is_valid=False, errors=["Bad content"])
    valid_score = scorer.score(entry, valid_result).total
    invalid_score = scorer.score(entry, invalid_result).total
    assert valid_score > invalid_score


def test_rich_metadata_improves_score():
    scorer = QualityScorer()
    from src.processors.schemas import ValidationResult
    validation = ValidationResult(is_valid=True)
    minimal = make_entry()
    rich = make_entry(title="A Title", description="A description", tags=["tag1", "tag2"])
    score_min = scorer.score(minimal, validation).total
    score_rich = scorer.score(rich, validation).total
    assert score_rich > score_min


# ── Pipeline ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_processes_entry():
    pipeline = NormalizationPipeline()
    entry = make_entry()
    result = await pipeline.process(entry)
    assert result.content_hash
    assert result.validation.is_valid
    assert 0 <= result.quality_score <= 100


@pytest.mark.asyncio
async def test_pipeline_invalid_entry():
    pipeline = NormalizationPipeline()
    entry = make_entry(content="tiny")
    result = await pipeline.process(entry)
    assert not result.validation.is_valid
