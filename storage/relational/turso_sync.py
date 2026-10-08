"""
Turso Cloud DB Asynchronous Write-Through Syncer
Ensures new memory records, agents, and namespaces are immediately pushed
to the Turso LibSQL Cloud database.
"""
import os
import threading
import logging
import json
import urllib.request

logger = logging.getLogger("turso_sync")

_DELETE_FENCE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memora_deleted_memories (
    tenant_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, memory_id)
)
"""

_DELETE_FENCE_SQL = """
INSERT INTO memora_deleted_memories (tenant_id, memory_id, deleted_at)
VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
ON CONFLICT(tenant_id, memory_id) DO NOTHING
"""

_MEMORY_UPSERT_SQL = """
INSERT INTO memory_records
    (id, namespace_id, owner_id, memory_type, content_text, source, confidence,
     importance, lifecycle_state, tenant_id, created_at)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
WHERE NOT EXISTS (
    SELECT 1 FROM memora_deleted_memories
    WHERE tenant_id = ? AND memory_id = ?
)
ON CONFLICT(id) DO UPDATE SET
    content_text = excluded.content_text,
    importance = excluded.importance,
    lifecycle_state = excluded.lifecycle_state
WHERE memory_records.tenant_id = excluded.tenant_id
  AND NOT EXISTS (
      SELECT 1 FROM memora_deleted_memories
      WHERE tenant_id = ? AND memory_id = ?
  )
"""


def _turso_credentials() -> tuple[str, str]:
    """Return configured Turso credentials without exposing them to logs."""
    from core.config import settings

    url = (os.getenv("TURSO_DATABASE_URL") or settings.TURSO_DATABASE_URL or "").strip()
    token = (os.getenv("TURSO_AUTH_TOKEN") or settings.TURSO_AUTH_TOKEN or "").strip()
    if not url or not token or "turso.io" not in url.lower():
        return "", ""
    return url.rstrip("/"), token


def turso_replica_enabled() -> bool:
    """Whether Turso is a secondary write-through replica, not the SQL primary."""
    url, token = _turso_credentials()
    if not url or not token:
        return False
    try:
        from storage.relational.session import storage_receipt

        return storage_receipt().get("backend") != "turso"
    except Exception:
        # If the primary backend cannot be identified, assume a replica may need
        # cleanup rather than silently retaining a remote copy.
        logger.exception("Could not determine authoritative SQL backend for Turso sync")
        return True


def _execute_pipeline(url: str, token: str, statements: list[dict]) -> bool:
    """Execute a Turso pipeline and report only explicit, error-free success."""
    pipeline_url = f"{url.rstrip('/')}/v2/pipeline"
    request = urllib.request.Request(
        pipeline_url,
        data=json.dumps({"requests": statements}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=6.0) as response:
            if getattr(response, "status", getattr(response, "code", None)) != 200:
                return False
            body = json.loads(response.read().decode("utf-8"))
        results = body.get("results")
        return (
            isinstance(results, list)
            and len(results) == len(statements)
            and all(isinstance(result, dict) and result.get("type") != "error" for result in results)
        )
    except Exception as exc:
        logger.warning("Turso write-through operation did not converge (%s)", type(exc).__name__)
        return False


def _stmt(sql: str, args: list[dict] | None = None) -> dict:
    return {
        "type": "execute",
        "stmt": {"sql": sql, **({"args": args} if args is not None else {})},
    }


def delete_memory_from_turso(memory_id: str, tenant_id: str = "default") -> bool:
    """Fence future replica upserts, then delete this tenant's mirrored record.

    The fence and delete are sent in a single ordered pipeline. Upserts consult
    that fence, so a delayed background write cannot resurrect a hard-deleted
    record after this operation succeeds.
    """
    url, token = _turso_credentials()
    if not url or not token:
        return False

    statements = [
        _stmt(_DELETE_FENCE_SCHEMA_SQL),
        _stmt(_DELETE_FENCE_SQL, [
            {"type": "text", "value": tenant_id},
            {"type": "text", "value": memory_id},
        ]),
        _stmt(
            "DELETE FROM memory_records WHERE tenant_id = ? AND id = ?",
            [
                {"type": "text", "value": tenant_id},
                {"type": "text", "value": memory_id},
            ],
        ),
    ]
    return _execute_pipeline(url, token, statements)


def _memory_statement(record_dict: dict) -> dict:
    """Build the fenced replica upsert for one memory."""
    memory_id = str(record_dict["id"])
    tenant_id = str(record_dict.get("tenant_id", "default"))
    args = [
        {"type": "text", "value": memory_id},
        {"type": "text", "value": record_dict["namespace_id"]},
        {"type": "text", "value": record_dict["owner_id"]},
        {"type": "text", "value": record_dict["memory_type"]},
        {"type": "text", "value": record_dict["content_text"]},
        {"type": "text", "value": record_dict.get("source", "api")},
        {"type": "float", "value": float(record_dict.get("confidence", 1.0))},
        {"type": "float", "value": float(record_dict.get("importance", 0.8))},
        {"type": "text", "value": record_dict.get("lifecycle_state", "active")},
        {"type": "text", "value": tenant_id},
        {"type": "text", "value": record_dict.get("created_at", "")},
        {"type": "text", "value": tenant_id},
        {"type": "text", "value": memory_id},
        {"type": "text", "value": tenant_id},
        {"type": "text", "value": memory_id},
    ]
    return _stmt(_MEMORY_UPSERT_SQL, args)


def _push_memory_worker(
    record_dict: dict,
    agent_dict: dict,
    namespace_dict: dict,
    turso_url: str,
    turso_token: str,
):
    """Write metadata and a tombstone-fenced memory to the optional replica."""
    stmt_agent = _stmt(
        """INSERT INTO agents (id, name, description, role, tenant_id)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
        [
            {"type": "text", "value": agent_dict["id"]},
            {"type": "text", "value": agent_dict["name"]},
            {"type": "text", "value": agent_dict.get("description", "")},
            {"type": "text", "value": agent_dict.get("role", "worker")},
            {"type": "text", "value": agent_dict.get("tenant_id", "default")},
        ],
    )
    stmt_namespace = _stmt(
        """INSERT INTO namespaces (id, path, type, agent_id, tenant_id)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
        [
            {"type": "text", "value": namespace_dict["id"]},
            {"type": "text", "value": namespace_dict["path"]},
            {"type": "text", "value": namespace_dict.get("type", "private")},
            {"type": "text", "value": namespace_dict.get("agent_id")},
            {"type": "text", "value": namespace_dict.get("tenant_id", "default")},
        ],
    )
    statements = [
        _stmt(_DELETE_FENCE_SCHEMA_SQL),
        stmt_agent,
        stmt_namespace,
        _memory_statement(record_dict),
    ]
    if _execute_pipeline(turso_url, turso_token, statements):
        logger.info("Turso replica write completed")


def push_memory_to_turso_async(record, agent=None, namespace=None):
    """
    Non-blocking async dispatcher to push a newly created memory to Turso Cloud.
    """
    if not turso_replica_enabled():
        # When Turso is already the authoritative ORM backend, the transaction
        # above has persisted the row. A second async upsert could race a later
        # hard delete and resurrect the row, so only mirror from a different
        # primary backend.
        return
    turso_url, turso_token = _turso_credentials()
    if not turso_url or not turso_token:
        return

    record_dict = {
        "id": record.id,
        "namespace_id": record.namespace_id,
        "owner_id": record.owner_id,
        "memory_type": record.memory_type.value if hasattr(record.memory_type, "value") else str(record.memory_type),
        "content_text": record.content_text,
        "source": getattr(record, "source", "api"),
        "confidence": getattr(record, "confidence", 1.0),
        "importance": getattr(record, "importance", 0.8),
        "lifecycle_state": record.lifecycle_state.value if hasattr(record.lifecycle_state, "value") else str(record.lifecycle_state),
        "tenant_id": getattr(record, "tenant_id", "default"),
        "created_at": record.created_at.isoformat() if hasattr(record, "created_at") and record.created_at else ""
    }

    agent_dict = {
        "id": agent.id if agent else record.owner_id,
        "name": agent.name if agent else "agent",
        "description": getattr(agent, "description", "") if agent else "",
        "role": getattr(agent, "role", "worker") if agent else "worker",
        "tenant_id": getattr(agent, "tenant_id", "default") if agent else "default"
    }

    namespace_dict = {
        "id": namespace.id if namespace else record.namespace_id,
        "path": namespace.path if namespace else f"memora://{agent_dict['name']}/private",
        "type": namespace.type.value if namespace and hasattr(namespace.type, "value") else "private",
        "agent_id": agent_dict["id"],
        "tenant_id": getattr(namespace, "tenant_id", "default") if namespace else "default"
    }

    t = threading.Thread(
        target=_push_memory_worker,
        args=(record_dict, agent_dict, namespace_dict, turso_url, turso_token),
        daemon=True
    )
    t.start()
