"""
Central configuration management using Pydantic Settings.
All secrets come from environment variables or GitHub Secrets — never hardcoded.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_")

    host: str = "localhost"
    port: int = 5432
    name: str = "tgbot"
    user: str = "tgbot"
    password: SecretStr = Field(..., description="PostgreSQL password")
    pool_size: int = 10
    max_overflow: int = 20
    pool_pre_ping: bool = True
    echo: bool = False

    @property
    def async_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def sync_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    bot_token: SecretStr = Field(..., description="Telegram Bot API token")
    admin_chat_id: int = Field(..., description="Admin chat ID for alerts")
    request_timeout: int = 30
    max_retries: int = 5
    flood_sleep_threshold: int = 60
    parse_mode: Literal["MarkdownV2", "HTML"] = "HTML"


class CollectorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COLLECTOR_")

    default_timeout: int = 30
    max_concurrent: int = 10
    retry_attempts: int = 3
    retry_wait_min: float = 1.0
    retry_wait_max: float = 10.0
    user_agent: str = (
        "Mozilla/5.0 (compatible; TGBotCollector/1.0; +https://github.com/your-org/tgbot)"
    )


class SchedulerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCHEDULER_")

    collection_cron: str = "0 */2 * * *"   # every 2 hours
    publishing_cron: str = "*/30 * * * *"  # every 30 minutes
    healthcheck_cron: str = "*/15 * * * *" # every 15 minutes
    cleanup_cron: str = "0 3 * * *"        # 3am daily
    timezone: str = "UTC"


class ScoringSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCORING_")

    freshness_weight: float = 0.35
    completeness_weight: float = 0.25
    source_reliability_weight: float = 0.25
    validation_weight: float = 0.15
    min_publishable_score: float = 40.0
    freshness_decay_hours: int = 72


class MonitoringSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MONITORING_")

    alert_on_consecutive_failures: int = 3
    prometheus_port: int = 9090
    enable_prometheus: bool = True
    health_check_url: str = ""


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "staging", "production"] = "production"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    debug: bool = False
    version: str = "1.0.0"

    # Nested settings — loaded from same .env with prefixes
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    collector: CollectorSettings = Field(default_factory=CollectorSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)

    @field_validator("debug", mode="before")
    @classmethod
    def debug_only_in_dev(cls, v: bool, info: object) -> bool:  # type: ignore[misc]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a cached singleton settings instance."""
    return AppSettings()
