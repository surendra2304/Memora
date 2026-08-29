"""
Event Emitter and Pub/Sub Pipeline for Memora
Publishes lifecycle events (memory.created, memory.updated, memory.shared, memory.superseded, context.generated, access.denied)
via Redis Pub/Sub with persistent in-memory queue fallback.
"""
import json
import logging
from typing import Dict, Any, List, Optional
from collections import deque
from datetime import datetime, timezone
from core.config import settings

logger = logging.getLogger(__name__)

class MemoraEvent:
    def __init__(self, event_type: str, payload: Dict[str, Any], timestamp: Optional[str] = None):
        self.event_type = event_type
        self.payload = payload
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp
        }

class EventEmitter:
    def __init__(self, channel: str = "memora:events", max_history: int = 500):
        self.channel = channel
        self.max_history = max_history
        self._history: deque = deque(maxlen=max_history)
        self._redis_client = None
        self._redis_connected = False

    def connect(self):
        try:
            import redis
            self._redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=1.5)
            self._redis_client.ping()
            self._redis_connected = True
            logger.info("Connected to Redis for Memora Event Bus.")
        except Exception as e:
            logger.warning(f"Could not connect to Redis: {e}. Event Bus will run in local in-memory fallback mode.")
            self._redis_connected = False

    def publish(self, event_type: str, payload: Dict[str, Any]) -> MemoraEvent:
        event = MemoraEvent(event_type=event_type, payload=payload)
        self._history.append(event)

        if self._redis_connected and self._redis_client:
            try:
                msg = json.dumps(event.to_dict())
                self._redis_client.publish(self.channel, msg)
            except Exception as e:
                logger.error(f"Failed to publish event to Redis: {e}")

        return event

    def get_recent_events(self, limit: int = 50, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        events = list(self._history)
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        events.reverse()
        return [e.to_dict() for e in events[:limit]]

event_emitter = EventEmitter()