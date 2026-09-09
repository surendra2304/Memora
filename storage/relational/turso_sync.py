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
import urllib.error

logger = logging.getLogger("turso_sync")

def _push_memory_worker(
    record_dict: dict,
    agent_dict: dict,
    namespace_dict: dict,
    turso_url: str,
    turso_token: str
):
    try:
        pipeline_url = f"{turso_url.rstrip('/')}/v2/pipeline"
        headers = {
            "Authorization": f"Bearer {turso_token}",
            "Content-Type": "application/json"
        }

        # 1. Upsert Agent
        stmt_agent = {
            "sql": """
                INSERT INTO agents (id, name, description, role, tenant_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING;
            """,
            "args": [
                {"type": "text", "value": agent_dict["id"]},
                {"type": "text", "value": agent_dict["name"]},
                {"type": "text", "value": agent_dict.get("description", "")},
                {"type": "text", "value": agent_dict.get("role", "worker")},
                {"type": "text", "value": agent_dict.get("tenant_id", "default")}
            ]
        }

        # 2. Upsert Namespace
        stmt_ns = {
            "sql": """
                INSERT INTO namespaces (id, path, type, agent_id, tenant_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING;
            """,
            "args": [
                {"type": "text", "value": namespace_dict["id"]},
                {"type": "text", "value": namespace_dict["path"]},
                {"type": "text", "value": namespace_dict.get("type", "private")},
                {"type": "text", "value": namespace_dict.get("agent_id")},
                {"type": "text", "value": namespace_dict.get("tenant_id", "default")}
            ]
        }

        # 3. Upsert Memory Record
        stmt_mem = {
            "sql": """
                INSERT INTO memory_records 
                (id, namespace_id, owner_id, memory_type, content_text, source, confidence, importance, lifecycle_state, tenant_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET 
                    content_text=excluded.content_text,
                    importance=excluded.importance,
                    lifecycle_state=excluded.lifecycle_state;
            """,
            "args": [
                {"type": "text", "value": record_dict["id"]},
                {"type": "text", "value": record_dict["namespace_id"]},
                {"type": "text", "value": record_dict["owner_id"]},
                {"type": "text", "value": record_dict["memory_type"]},
                {"type": "text", "value": record_dict["content_text"]},
                {"type": "text", "value": record_dict.get("source", "api")},
                {"type": "float", "value": float(record_dict.get("confidence", 1.0))},
                {"type": "float", "value": float(record_dict.get("importance", 0.8))},
                {"type": "text", "value": record_dict.get("lifecycle_state", "active")},
                {"type": "text", "value": record_dict.get("tenant_id", "default")},
                {"type": "text", "value": record_dict.get("created_at", "")}
            ]
        }

        payload = {
            "requests": [
                {"type": "execute", "stmt": stmt_agent},
                {"type": "execute", "stmt": stmt_ns},
                {"type": "execute", "stmt": stmt_mem}
            ]
        }

        req = urllib.request.Request(
            pipeline_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            if resp.status == 200:
                logger.info(f"Successfully pushed memory {record_dict['id']} to Turso Cloud")
    except Exception as e:
        logger.debug(f"Turso Cloud write-through background push skipped: {e}")


def push_memory_to_turso_async(record, agent=None, namespace=None):
    """
    Non-blocking async dispatcher to push a newly created memory to Turso Cloud.
    """
    turso_url = os.getenv("TURSO_DATABASE_URL")
    turso_token = os.getenv("TURSO_AUTH_TOKEN")
    if not (turso_url and turso_token and "turso.io" in turso_url):
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
