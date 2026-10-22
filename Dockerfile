FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# Install declared runtime dependencies only.
RUN pip install --no-cache-dir . \
    && mkdir -p /app/data /app/logs \
    && useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app /home/appuser

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    HOME=/home/appuser

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Automatically run database migrations before serving; exec replaces shell as PID 1
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
