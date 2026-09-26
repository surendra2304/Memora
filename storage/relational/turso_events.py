"""Turso-backed durable event log access through the libSQL HTTP pipeline."""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Any

from core.config import settings

_schema_lock = threading.Lock()
_schema_ready_for: set[str] = set()


def production_mode() -> bool:
    return (os.getenv("ENVIRONMENT", "") or settings.MEMORA_ENV).lower() == "production"


def _credentials() -> tuple[str, str]:
    enabled = os.getenv("MEMORA_TURSO_EVENTS_ENABLED", "").lower() in {"1", "true", "yes"}
    production = production_mode()
    if not enabled and not production:
        return "", ""
    url = (os.getenv("TURSO_DATABASE_URL") or settings.TURSO_DATABASE_URL or "").strip().rstrip("/")
    token = (os.getenv("TURSO_AUTH_TOKEN") or settings.TURSO_AUTH_TOKEN or "").strip()
    if "turso.io" not in url or not token:
        return "", ""
    return url, token


def configured() -> bool:
    return all(_credentials())


def _request(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    url, token = _credentials()
    if not url or not token:
        raise RuntimeError("Turso event store is not configured")
    req = urllib.request.Request(
        f"{url}/v2/pipeline",
        data=json.dumps({"requests": requests}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8.0) as response:
        if response.status != 200:
            raise RuntimeError(f"Turso returned HTTP {response.status}")
        body = json.loads(response.read().decode("utf-8"))
    results = body.get("results")
    if not isinstance(results, list):
        raise RuntimeError("Turso returned an invalid pipeline response")
    for result in results:
        if result.get("type") == "error":
            raise RuntimeError("Turso event operation failed")
    return results


def _statement(sql: str, args: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"type": "execute", "stmt": {"sql": sql, **({"args": args} if args is not None else {})}}


def _text(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    return {"type": "text", "value": str(value)}


def _integer(value: int) -> dict[str, Any]:
    return {"type": "integer", "value": str(int(value))}


def ensure_schema() -> None:
    url, _ = _credentials()
    if not url or url in _schema_ready_for:
        return
    with _schema_lock:
        if url in _schema_ready_for:
            return
        _request([
            _statement("CREATE TABLE IF NOT EXISTS event_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL, tenant_id TEXT NOT NULL DEFAULT 'default', target_agent TEXT, payload TEXT NOT NULL, created_at TEXT NOT NULL)"),
            _statement("CREATE INDEX IF NOT EXISTS ix_event_log_tenant_cursor ON event_log(tenant_id, id)"),
            _statement("CREATE INDEX IF NOT EXISTS ix_event_log_target_cursor ON event_log(target_agent, id)"),
            _statement("CREATE TABLE IF NOT EXISTS event_consumer_cursors (tenant_id TEXT NOT NULL, agent TEXT NOT NULL, consumer_id TEXT NOT NULL DEFAULT 'default', last_event_id INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, PRIMARY KEY(tenant_id, agent, consumer_id))"),
        ])
        _schema_ready_for.add(url)


def probe() -> bool:
    ensure_schema()
    _request([_statement("SELECT 1")])
    return True


def _row_values(row: list[dict[str, Any]]) -> list[Any]:
    return [column.get("value") if isinstance(column, dict) else column for column in row]


def _result_rows(result: dict[str, Any]) -> list[list[dict[str, Any]]]:
    return result.get("response", {}).get("result", {}).get("rows", [])


def append(event: dict[str, Any]) -> int:
    ensure_schema()
    _request([
        _statement(
            "INSERT INTO event_log(event_id,event_type,tenant_id,target_agent,payload,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING",
            [
                _text(event["event_id"]), _text(event["event_type"]),
                _text(event.get("tenant_id", "default")), _text(event.get("target_agent")),
                _text(json.dumps(event.get("payload", {}), separators=(",", ":"), default=str)),
                _text(event["created_at"]),
            ],
        )
    ])
    result = _request([_statement("SELECT id FROM event_log WHERE event_id = ? LIMIT 1", [_text(event["event_id"])])])[0]
    rows = _result_rows(result)
    if not rows:
        raise RuntimeError("Turso did not confirm the event cursor")
    return int(_row_values(rows[0])[0])


def read(*, after_id: int, limit: int, tenant_id: str, agent: str, event_type: str | None = None) -> list[dict[str, Any]]:
    ensure_schema()
    sql = "SELECT id,event_id,event_type,tenant_id,target_agent,payload,created_at FROM event_log WHERE id>? AND tenant_id=? AND (target_agent IS NULL OR target_agent=?)"
    args = [
        _integer(after_id),
        _text(tenant_id),
        _text(agent),
    ]
    if event_type:
        sql += " AND event_type=?"
        args.append(_text(event_type))
    sql += " ORDER BY id ASC LIMIT ?"
    args.append(_integer(limit))
    rows = _result_rows(_request([_statement(sql, args)])[0])
    events = []
    for raw in rows:
        values = _row_values(raw)
        payload = json.loads(values[5]) if isinstance(values[5], str) else (values[5] or {})
        events.append({
            "id": int(values[0]),
            "event_id": values[1],
            "event_type": values[2],
            "tenant_id": values[3],
            "target_agent": values[4],
            "payload": payload,
            "created_at": values[6],
        })
    return events


def read_cursor(*, tenant_id: str, agent: str, consumer_id: str = "default") -> int:
    ensure_schema()
    result = _request([_statement(
        "SELECT last_event_id FROM event_consumer_cursors WHERE tenant_id=? AND agent=? AND consumer_id=? LIMIT 1",
        [_text(tenant_id), _text(agent), _text(consumer_id)],
    )])[0]
    rows = _result_rows(result)
    return int(_row_values(rows[0])[0]) if rows else 0


def acknowledge(*, tenant_id: str, agent: str, consumer_id: str = "default", event_id: int) -> int:
    """Advance only to a persisted event that this agent is permitted to read."""
    ensure_schema()
    current = read_cursor(tenant_id=tenant_id, agent=agent, consumer_id=consumer_id)
    if event_id <= current:
        return current
    visible = _request([_statement(
        "SELECT id FROM event_log WHERE id>? AND id<=? AND tenant_id=? AND (target_agent IS NULL OR target_agent=?) ORDER BY id ASC LIMIT 1",
        [_integer(current), _integer(event_id), _text(tenant_id), _text(agent)],
    )])[0]
    rows = _result_rows(visible)
    if not rows or int(_row_values(rows[0])[0]) != event_id:
        raise ValueError("Event is not the next visible event for this consumer")
    now = datetime.now(timezone.utc).isoformat()
    _request([_statement(
        "INSERT INTO event_consumer_cursors(tenant_id,agent,consumer_id,last_event_id,updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(tenant_id,agent,consumer_id) DO UPDATE SET last_event_id=MAX(last_event_id,excluded.last_event_id),updated_at=excluded.updated_at",
        [_text(tenant_id), _text(agent), _text(consumer_id), _integer(event_id), _text(now)],
    )])
    return read_cursor(tenant_id=tenant_id, agent=agent, consumer_id=consumer_id)
