"""Durable event notifications for agents that poll or reconnect."""
import hashlib
import hmac
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import authenticate_agent
from storage.relational.models import EventConsumerCursor, EventLog
from storage.relational.session import get_db
from storage.relational.turso_events import acknowledge as acknowledge_turso_event, append as append_turso_event, configured as turso_events_configured, production_mode as turso_required, read as read_turso_events, read_cursor as read_turso_cursor

router = APIRouter(prefix="/v1/events", tags=["Agent Event Feed"])
mesh_router = APIRouter(tags=["Agent Event Ingest"])

_AGENTS = {"friday", "memora", "inference", "stratex", "intelx", "futuris", "cortex", "forge", "sentinel"}


class IncomingEnvelope(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    from_agent: str = Field(min_length=1, max_length=64)
    to_agent: str = Field(min_length=1, max_length=64)
    intent: str = Field(min_length=1, max_length=128)
    priority: str = "normal"
    ttl: int = Field(default=300, ge=1, le=86400)
    auth_token: str | None = None
    signature: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class EventAcknowledgement(BaseModel):
    event_id: int = Field(gt=0)
    consumer_id: str = Field(default="default", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")

_VISIBLE_FIELDS = {
    "memory.created": ("memory_id", "tenant_id", "owner", "namespace", "type"),
    "memory.updated": ("memory_id", "action", "hard", "actor"),
    "memory.shared": ("memory_id", "shared_by", "shared_with", "namespace_path", "actions"),
    "memory.superseded": ("superseded_id", "winner_id", "actor"),
    "memory.promoted": ("memory_id", "promoted_by", "new_type"),
    "context.generated": ("bundle_id", "agent", "memories_count", "is_degraded"),
    "access.denied": ("actor_id", "memory_id", "rule"),
    "intelx.news": ("source_agent", "signal_id", "headline", "summary", "published_at", "source_url", "sources", "relevance", "topics", "correlation_id"),
    "futuris.forecast": (
        "source_agent", "forecast_id", "target", "status", "as_of", "expires_at",
        "model_version", "prediction", "range_lower", "range_upper", "probability",
        "confidence", "prediction_is_not_authorization", "correlation_id",
    ),
    "stratex.decision": (
        "source_agent", "decision_id", "forecast_id", "correlation_id", "action",
        "paper_only", "policy_reason", "gates_summary", "headline", "summary", "decided_at",
    ),
}


def _validate_futuris_forecast(payload: dict[str, Any]) -> None:
    """Reject malformed or authority-bearing forecast events before cursor publication."""
    forecast_id = payload.get("forecast_id")
    target = payload.get("target")
    state = payload.get("status")
    if not isinstance(forecast_id, str) or not forecast_id.strip() or len(forecast_id) > 128:
        raise HTTPException(status_code=422, detail="Forecast event requires a bounded forecast_id")
    if not isinstance(target, str) or not target.strip() or len(target) > 500:
        raise HTTPException(status_code=422, detail="Forecast event requires a bounded target")
    if not isinstance(state, str) or state not in {"active", "resolved", "expired", "invalidated"}:
        raise HTTPException(status_code=422, detail="Forecast event has an invalid lifecycle status")
    for field, max_length in (("as_of", 64), ("expires_at", 64), ("model_version", 128)):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > max_length):
            raise HTTPException(status_code=422, detail=f"Forecast event has invalid {field}")
    if payload.get("prediction_is_not_authorization") is not True:
        raise HTTPException(status_code=422, detail="Forecast events must remain advisory")

    def finite_number(field: str, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(status_code=422, detail=f"Forecast event requires numeric {field}")
        try:
            number = float(value)
        except (OverflowError, ValueError):
            raise HTTPException(status_code=422, detail=f"Forecast event has invalid {field}") from None
        if not math.isfinite(number):
            raise HTTPException(status_code=422, detail=f"Forecast event has non-finite {field}")
        return number

    values: dict[str, float] = {}
    for field in ("prediction", "range_lower", "range_upper", "confidence"):
        values[field] = finite_number(field, payload.get(field))
    probability = payload.get("probability")
    if probability is not None:
        probability = finite_number("probability", probability)
    if not 0.0 <= values["confidence"] <= 1.0 or (
        probability is not None and not 0.0 <= probability <= 1.0
    ):
        raise HTTPException(status_code=422, detail="Forecast event confidence/probability is out of range")
    if not values["range_lower"] <= values["prediction"] <= values["range_upper"]:
        raise HTTPException(status_code=422, detail="Forecast interval must contain its prediction")


def _validate_stratex_decision(payload: dict[str, Any]) -> None:
    """Reject malformed or live-bearing decision receipts before cursor publication."""
    decision_id = payload.get("decision_id")
    if not isinstance(decision_id, str) or not decision_id.strip() or len(decision_id) > 128:
        raise HTTPException(status_code=422, detail="Decision event requires a bounded decision_id")
    forecast_id = payload.get("forecast_id")
    if not isinstance(forecast_id, str) or not forecast_id.strip() or len(forecast_id) > 128:
        raise HTTPException(status_code=422, detail="Decision event requires a bounded forecast_id")
    if payload.get("action") not in {"paper_intent", "no_action", "watch"}:
        raise HTTPException(status_code=422, detail="Decision action must be a paper-mode action")
    if payload.get("paper_only") is not True:
        raise HTTPException(status_code=422, detail="Stratex decisions are paper-only by design")
    policy_reason = payload.get("policy_reason")
    if not isinstance(policy_reason, str) or not policy_reason.strip() or len(policy_reason) > 500:
        raise HTTPException(status_code=422, detail="Decision event requires a bounded policy_reason")
    gates = payload.get("gates_summary")
    if gates is not None:
        if (
            not isinstance(gates, list)
            or len(gates) > 12
            or not all(isinstance(item, str) and len(item) <= 200 for item in gates)
        ):
            raise HTTPException(status_code=422, detail="Decision gates_summary must be a list of at most 12 short strings")
    for field, max_length in (("headline", 500), ("summary", 2000), ("decided_at", 64)):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > max_length):
            raise HTTPException(status_code=422, detail=f"Decision event has invalid {field}")


@router.get("")
def read_events(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = Query(default=None, min_length=1, max_length=128),
    agent: str = Depends(authenticate_agent),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return ordered, replayable notification metadata after a consumer cursor."""
    # Agent credentials currently identify ecosystem services, not tenants. Keep
    # this feed on the shared default tenant until key-to-tenant grants exist.
    tenant_id = "default"
    query = db.query(EventLog).filter(
        EventLog.id > after_id,
        EventLog.tenant_id == tenant_id,
        (EventLog.target_agent.is_(None) | (EventLog.target_agent == agent)),
    )
    if event_type:
        query = query.filter(EventLog.event_type == event_type)
    if turso_events_configured():
        try:
            remote_rows = read_turso_events(after_id=after_id, limit=limit, tenant_id=tenant_id, agent=agent, event_type=event_type)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Durable event store is temporarily unavailable") from exc
        rows = remote_rows
    elif turso_required():
        raise HTTPException(status_code=503, detail="Durable event store is not configured")
    else:
        rows = query.order_by(EventLog.id.asc()).limit(limit).all()
    events = []
    for row in rows:
        allowed = _VISIBLE_FIELDS.get(row["event_type"] if isinstance(row, dict) else row.event_type, ())
        raw_payload = row["payload"] if isinstance(row, dict) else row.payload
        payload = {key: raw_payload[key] for key in allowed if key in raw_payload}
        events.append({
            "id": row["id"] if isinstance(row, dict) else row.id,
            "event_id": row["event_id"] if isinstance(row, dict) else row.event_id,
            "event_type": row["event_type"] if isinstance(row, dict) else row.event_type,
            "tenant_id": row["tenant_id"] if isinstance(row, dict) else row.tenant_id,
            "created_at": row["created_at"] if isinstance(row, dict) else (row.created_at.isoformat() if row.created_at else None),
            "payload": payload,
        })
    return {
        "agent": agent,
        "events": events,
        "next_after_id": events[-1]["id"] if events else after_id,
        "has_more": len(events) == limit,
    }


@router.get("/cursor")
def read_consumer_cursor(
    consumer_id: str = "default",
    agent: str = Depends(authenticate_agent),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return this agent's independent durable event acknowledgement cursor."""
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", consumer_id):
        raise HTTPException(status_code=422, detail="Invalid consumer_id")
    if turso_events_configured():
        try:
            cursor = read_turso_cursor(tenant_id="default", agent=agent, consumer_id=consumer_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Durable event cursor is temporarily unavailable") from exc
    elif turso_required():
        raise HTTPException(status_code=503, detail="Durable event store is not configured")
    else:
        row = db.query(EventConsumerCursor).filter_by(tenant_id="default", agent=agent, consumer_id=consumer_id).first()
        cursor = row.last_event_id if row else 0
    return {"agent": agent, "consumer_id": consumer_id, "after_id": cursor}


@router.post("/ack")
def acknowledge_event(
    acknowledgement: EventAcknowledgement,
    agent: str = Depends(authenticate_agent),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Acknowledge one event after successful handling; reads alone never advance the cursor."""
    if turso_events_configured():
        try:
            cursor = acknowledge_turso_event(tenant_id="default", agent=agent, consumer_id=acknowledgement.consumer_id, event_id=acknowledgement.event_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Event is not the next visible event for this consumer") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Durable event cursor is temporarily unavailable") from exc
    elif turso_required():
        raise HTTPException(status_code=503, detail="Durable event store is not configured")
    else:
        row = db.query(EventConsumerCursor).filter_by(tenant_id="default", agent=agent, consumer_id=acknowledgement.consumer_id).first()
        current = row.last_event_id if row else 0
        if acknowledgement.event_id <= current:
            return {"status": "acknowledged", "agent": agent, "consumer_id": acknowledgement.consumer_id, "after_id": current}
        next_visible = db.query(EventLog).filter(
            EventLog.id > current,
            EventLog.id <= acknowledgement.event_id,
            EventLog.tenant_id == "default",
            (EventLog.target_agent.is_(None) | (EventLog.target_agent == agent)),
        ).order_by(EventLog.id.asc()).first()
        if next_visible is None or next_visible.id != acknowledgement.event_id:
            raise HTTPException(status_code=409, detail="Event is not the next visible event for this consumer")
        if row is None:
            row = EventConsumerCursor(tenant_id="default", agent=agent, consumer_id=acknowledgement.consumer_id, last_event_id=acknowledgement.event_id)
            db.add(row)
        else:
            row.last_event_id = max(row.last_event_id, acknowledgement.event_id)
        db.commit()
        cursor = row.last_event_id
    return {"status": "acknowledged", "agent": agent, "consumer_id": acknowledgement.consumer_id, "after_id": cursor}


@mesh_router.post("/mesh/envelope", status_code=202)
def ingest_envelope(
    envelope: IncomingEnvelope,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Persist a signed-in agent notification for recipient cursor polling."""
    sender = envelope.from_agent.lower()
    recipient = envelope.to_agent.lower()
    key = os.getenv(f"{sender.upper()}_API_KEY", "") if sender in _AGENTS else ""
    production = os.getenv("ENVIRONMENT", "").lower() == "production"
    supplied = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
    if production or key or supplied:
        if not key or not supplied or not hmac.compare_digest(key, supplied):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid sender credentials")
    if sender not in _AGENTS or recipient not in _AGENTS | {"all"}:
        raise HTTPException(status_code=422, detail="Unknown sender or recipient")
    if envelope.intent == "futuris.forecast":
        if sender != "futuris":
            raise HTTPException(status_code=403, detail="Only authenticated Futuris may publish forecast advisories")
        if not envelope.message_id.startswith("futuris-"):
            raise HTTPException(status_code=422, detail="Forecast event IDs must use the futuris- prefix")
        _validate_futuris_forecast(envelope.payload)
    if envelope.intent == "stratex.decision":
        if sender != "stratex":
            raise HTTPException(status_code=403, detail="Only authenticated Stratex may publish paper decision receipts")
        if not envelope.message_id.startswith("stratex-"):
            raise HTTPException(status_code=422, detail="Decision event IDs must use the stratex- prefix")
        _validate_stratex_decision(envelope.payload)
    if envelope.created_at > time.time() + 60 or time.time() - envelope.created_at > envelope.ttl:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Envelope expired or timestamp is invalid")
    if len(json.dumps(envelope.payload, default=str)) > 262144:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Envelope payload exceeds 256 KiB")
    if production and not envelope.signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signed envelopes are required")
    if envelope.signature:
        signed_fields = {
            "message_id": envelope.message_id,
            "correlation_id": envelope.correlation_id,
            "from_agent": envelope.from_agent,
            "to_agent": envelope.to_agent,
            "intent": envelope.intent,
            "priority": envelope.priority,
            "ttl": envelope.ttl,
            "auth_token": envelope.auth_token,
            "payload": envelope.payload,
            "created_at": envelope.created_at,
        }
        raw = json.dumps(signed_fields, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        expected = hmac.new(key.encode("utf-8"), raw, hashlib.sha256).hexdigest() if key else ""
        if not expected or not hmac.compare_digest(expected, envelope.signature):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid envelope signature")

    existing = db.query(EventLog).filter(EventLog.event_id == envelope.message_id).first()
    if existing:
        return {"status": "duplicate", "event_id": existing.event_id, "cursor": existing.id}

    event_data = {
        "event_id": envelope.message_id,
        "event_type": envelope.intent,
        "tenant_id": "default",
        "target_agent": None if recipient == "all" else recipient,
        "payload": {**envelope.payload, "source_agent": sender, "correlation_id": envelope.correlation_id},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    remote_cursor = None
    if turso_events_configured():
        try:
            remote_cursor = append_turso_event(event_data)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Durable event store is temporarily unavailable") from exc
    elif turso_required():
        raise HTTPException(status_code=503, detail="Durable event store is not configured")

    row = EventLog(
        event_id=envelope.message_id,
        event_type=envelope.intent,
        tenant_id="default",
        target_agent=event_data["target_agent"],
        payload=event_data["payload"],
        cloud_synced=remote_cursor is not None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "accepted", "event_id": row.event_id, "cursor": remote_cursor or row.id}
