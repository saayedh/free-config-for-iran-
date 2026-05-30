FROM python:3.12-slim

# Security: run as non-root
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Install system dependencies for lxml and asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev \
    libxslt-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e "."

COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./

RUN chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["python", "-m", "src.main"]
CMD ["bot"]
