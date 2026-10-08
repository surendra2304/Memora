FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data /app/logs \
    && groupadd --system memora \
    && useradd --system --gid memora --create-home --home-dir /home/memora --shell /usr/sbin/nologin memora \
    && chown -R memora:memora /app/data /app/logs /home/memora

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    HOME=/home/memora

USER memora

# Do not serve against an out-of-date schema. A failed migration is a deploy
# failure and must stop startup rather than silently leaving the API degraded.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
