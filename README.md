# TGBot Collector

> Production-grade Telegram bot for automated data collection, processing, and publishing.

[![CI](https://github.com/your-org/tgbot/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/tgbot/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/your-org/tgbot/branch/main/graph/badge.svg)](https://codecov.io/gh/your-org/tgbot)
[![Python 3.12](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        GitHub Actions                            │
│  ┌────────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────┐  │
│  │ collection │  │  publishing  │  │  health  │  │    CI    │  │
│  │ (*/2h)     │  │  (*/30min)   │  │ (*/15min)│  │  (push)  │  │
│  └─────┬──────┘  └──────┬───────┘  └────┬─────┘  └──────────┘  │
└────────┼────────────────┼───────────────┼────────────────────────┘
         ▼                ▼               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Python Application                           │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Collection Orchestrator                     │    │
│  │   Collector A  │  Collector B  │  Collector C  │  ...   │    │
│  └─────────────────────────┬───────────────────────────────┘    │
│                             ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │            Normalization Pipeline                         │   │
│  │  Hash → Validate → Score → Deduplicate                   │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             ▼                                    │
│  ┌────────────────────────────────────────────────────────┐     │
│  │            Telegram Publisher                           │     │
│  │  Render → Flood Control → Retry → Broadcast            │     │
│  └────────────────────────────────────────────────────────┘     │
└─────────────────────────────┬───────────────────────────────────┘
                              ▼
                    ┌──────────────────┐
                    │   PostgreSQL     │
                    │  (sources,       │
                    │   entries,       │
                    │   channels,      │
                    │   posts, logs)   │
                    └──────────────────┘
```

---

## Quick Start

### 1. Clone and Configure

```bash
git clone https://github.com/your-org/tgbot.git
cd tgbot
cp .env.example .env
# Edit .env with your credentials
```

### 2. Start the Database

```bash
docker compose up db -d
```

### 3. Install and Migrate

```bash
pip install -e ".[dev]"
alembic upgrade head
```

### 4. Run the Bot

```bash
# Long-running bot with scheduler
python -m src.main bot

# One-shot collection (for CI/CD)
python -m src.main collect

# One-shot publish
python -m src.main publish

# Health check (exits 0/1)
python -m src.main health
```

---

## Project Structure

```
tgbot/
├── src/
│   ├── main.py                    # CLI entrypoint
│   ├── config.py                  # Pydantic settings
│   ├── collectors/
│   │   ├── base.py                # Abstract BaseCollector
│   │   ├── implementations.py     # Concrete collectors + registry
│   │   └── orchestrator.py        # Parallel collection runner
│   ├── processors/
│   │   ├── schemas.py             # Pydantic data models
│   │   └── normalizer.py          # Hash + validate + score pipeline
│   ├── database/
│   │   ├── models.py              # SQLAlchemy ORM models
│   │   ├── engine.py              # Async engine & session factory
│   │   └── repositories.py        # Repository pattern (all SQL here)
│   ├── telegram/
│   │   ├── publisher.py           # Broadcast engine + flood control
│   │   └── handlers.py            # Bot command handlers
│   ├── scheduler/
│   │   └── scheduler.py           # APScheduler job definitions
│   ├── monitoring/
│   │   └── health.py              # Health checks + alerts + cleanup
│   └── utils/
│       └── logging.py             # structlog configuration
├── tests/
│   ├── unit/
│   │   ├── test_normalizer.py
│   │   └── test_collectors.py
│   └── integration/
├── migrations/
│   ├── env.py
│   └── versions/
│       └── 0001_initial.py
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── collection.yml
│       ├── publishing.yml
│       └── health.yml
├── docker/
│   └── Dockerfile
├── docker-compose.yml
├── alembic.ini
├── pyproject.toml
└── .env.example
```

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `sources` | Registered data sources with health tracking |
| `entries` | Normalized, hashed, scored data entries |
| `channels` | Telegram channels with admin verification |
| `posts` | Publishing audit trail per entry×channel |
| `fetch_logs` | Immutable per-source collection audit logs |
| `system_logs` | Structured application logs persisted to DB |
| `jobs` | Scheduled job execution tracking |
| `settings` | Runtime key-value configuration |

---

## Adding a New Collector

Create a new class in `src/collectors/implementations.py`:

```python
class MySourceCollector(BaseCollector):
    source_id = "my_source"
    source_name = "My Data Source"
    source_url = "https://api.mysource.com/feed"

    async def parse(self, raw: RawEntry) -> list[NormalizedEntry]:
        data = json.loads(raw.raw_content)
        return [
            NormalizedEntry(
                source_id=self.source_id,
                content=item["text"],
                fetched_at=raw.fetched_at,
                source_url=raw.source_url,
            )
            for item in data["items"]
            if item.get("text")
        ]

# Register it:
COLLECTOR_REGISTRY["my_source"] = MySourceCollector
```

Then insert a row in the `sources` table:

```sql
INSERT INTO sources (id, source_id, source_name, source_url, collector_class)
VALUES (gen_random_uuid(), 'my_source', 'My Data Source',
        'https://api.mysource.com/feed', 'MySourceCollector');
```

---

## GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_ADMIN_CHAT_ID` | Your chat ID for alerts |

---

## Channel Registration

1. Add the bot to your Telegram channel as **Administrator**
2. Send `/register` in the channel
3. The bot verifies admin status and activates broadcasting

---

## Quality Scoring

Each entry receives a `quality_score` from 0–100:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Freshness | 35% | Exponential decay over 72 hours |
| Completeness | 25% | Bonus for title, description, tags |
| Source Reliability | 25% | Based on source success rate |
| Validation | 15% | Penalty per validation error |

Only entries scoring ≥ 40 (configurable) are published.

---

## License

MIT
