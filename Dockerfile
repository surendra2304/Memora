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
RUN mkdir -p /app/data

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

# Run migrations before serving. Previously the container relied purely on
# Base.metadata.create_all() at import time, which never ALTERs an existing
# table. That is why a database carrying an older schema crashed on startup
# with "no such column: event_log.target_agent" and could never be repaired:
# the migration that owned that column did a bare CREATE TABLE against a table
# create_all had already built. The migrations are now convergent, so running
# them here is safe and makes the schema deterministic on every deploy.
#
# `|| true` is deliberate: a failure must still let the process start and report
# its own health truthfully rather than crash-looping with an opaque exit code.
CMD ["sh", "-c", "alembic upgrade head || echo '[migrate] continuing; app will report its own health'; exec uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
