"""
Event Emitter and Pub/Sub Pipeline for Memora
Publishes lifecycle events (memory.created, memory.updated, memory.shared, memory.superseded, context.generated, access.denied)
via Redis Pub/Sub with persistent in-memory queue fallback. Events are durable in the
authoritative Oracle relational store via the ORM session passed by callers.
"""
import json
import logging
import os
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional
from collections import deque
from datetime import datetime, timezone
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import Session
from core.config import settings

logger = logging.getLogger(__name__)

class MemoraEvent:
    def __init__(self, event_type: str, payload: Dict[str, Any], timestamp: Optional[str] = None, event_id: Optional[str] = None, cursor: Optional[int] = None):
        self.event_type = event_type
        self.payload = payload
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.event_id = event_id or str(uuid.uuid4())
        self.cursor = cursor

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
            "cursor": self.cursor,
        }

class EventEmitter:
    def __init__(self, channel: str = "memora:events", max_history: int = 500):
        self.channel = channel
        self.max_history = max_history
        self._history: deque = deque(maxlen=max_history)
        self._redis_client = None
        self._redis_connected = False

    def connect(self):
        redis_url = getattr(settings, "REDIS_URL", "")
        if not redis_url or redis_url.lower() in ("none", "disabled", "false", ""):
            logger.info("Redis not configured. Event Bus running in local in-memory mode.")
            self._redis_connected = False
            return

        if "localhost" in redis_url and (getattr(settings, "MEMORA_ENV", "").lower() == "production" or os.getenv("ENVIRONMENT", "").lower() == "production"):
            logger.info("Render cloud environment detected without external Redis. Event Bus running in local in-memory mode.")
            self._redis_connected = False
            return

        try:
            import redis
            self._redis_client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=1.5)
            self._redis_client.ping()
            self._redis_connected = True
            logger.info("Connected to Redis for Memora Event Bus.")
        except Exception as e:
            logger.info(f"Redis unavailable ({e}). Event Bus running in local in-memory mode.")
            self._redis_connected = False

    def publish(self, event_type: str, payload: Dict[str, Any], db=None) -> MemoraEvent:
        event = MemoraEvent(event_type=event_type, payload=payload)

        # When a caller already owns the memory transaction, put the event in
        # that transaction so it cannot be delivered for a rolled-back write.
        try:
            from storage.relational.models import EventLog
            if db is not None:
                row = EventLog(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    tenant_id=str(payload.get("tenant_id") or "default"),
                    target_agent=payload.get("target_agent"),
                    payload=payload,
                )
                db.add(row)
                db.flush()
                event.cursor = row.id
                db.info.setdefault("memora_pending_event_sync", []).append(row.event_id)
                db.info.setdefault("memora_pending_event_notifications", []).append((self, event))
        except Exception:
            logger.exception("Could not persist Memora event %s", event.event_type)
            if db is not None:
                raise

        if db is None:
            self._notify_after_commit(event)
        else:
            # Do not expose an event in memory or Redis until its DB transaction commits.
            pass

        return event

    def _notify_after_commit(self, event: MemoraEvent) -> None:
        self._history.append(event)
        if self._redis_connected and self._redis_client:
            try:
                self._redis_client.publish(self.channel, json.dumps(event.to_dict()))
            except Exception as exc:
                logger.error("Failed to publish event to Redis: %s", type(exc).__name__)

    def get_recent_events(self, limit: int = 50, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        events = list(self._history)
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        events.reverse()
        return [e.to_dict() for e in events[:limit]]


def _after_commit(session: Session) -> None:
    event_ids = session.info.pop("memora_pending_event_sync", [])
    notifications = session.info.pop("memora_pending_event_notifications", [])
    for emitter, event in notifications:
        emitter._notify_after_commit(event)
    if event_ids:
        logger.debug("Durable events committed: %d", len(event_ids))


def _after_rollback(session: Session) -> None:
    session.info.pop("memora_pending_event_sync", None)
    session.info.pop("memora_pending_event_notifications", None)


def start_cloud_sync(interval_sec: float = 30.0) -> bool:
    """No-op kept for startup wiring: the durable store is Oracle, already authoritative."""
    return True


def stop_cloud_sync() -> None:
    """No-op kept for shutdown wiring."""


sqlalchemy_event.listen(Session, "after_commit", _after_commit)
sqlalchemy_event.listen(Session, "after_rollback", _after_rollback)


event_emitter = EventEmitter()
