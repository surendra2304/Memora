"""
Universal Memora Client SDK for FRIDAY Universe Agents
Provides fail-safe persistent memory recording, semantic fact extraction,
and context recall across all 9 autonomous subsystems.
"""
import os
import json
import logging
import sqlite3
import uuid
import time
from typing import List, Dict, Any, Optional
import urllib.request
import urllib.parse
import urllib.error

logger = logging.getLogger("memora_client")

class MemoraClient:
    """
    Universal client for connecting any agent to the Memora Memory Fabric.
    Automatically handles network failovers with local SQLite fallback.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        local_db_path: Optional[str] = None,
        timeout: float = 4.0
    ):
        self.base_url = (base_url or os.getenv("MEMORA_URL", "http://localhost:8000")).rstrip("/")
        self.api_key = api_key or os.getenv("MEMORA_API_KEY", "memora_api")
        self.timeout = timeout
        
        # Local fallback DB path
        if local_db_path:
            self.local_db_path = local_db_path
        else:
            default_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memora.db")
            self.local_db_path = default_path if os.path.exists(default_path) else "data/memora.db"

    def record_interaction(
        self,
        agent_name: str,
        user_input: str,
        agent_output: str,
        event_type: str = "dialogue",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Record a full conversational turn or operational action into Memora.
        Automatically extracts semantic facts, user preferences, and episodic traces.
        """
        payload = {
            "agent_name": agent_name.lower(),
            "user_text": user_input,
            "agent_text": agent_output,
            "event_type": event_type,
            "tags": tags or [],
            "metadata": metadata or {}
        }
        
        url = f"{self.base_url}/v1/memories/record-interaction"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-Agent-Name": agent_name.lower()
        }

        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status in (200, 201):
                    return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"Memora API unavailable ({e}), falling back to direct local storage.")

        # Local fallback
        return self._record_locally(agent_name, user_input, agent_output, event_type, tags, metadata)

    def record_fact(
        self,
        agent_name: str,
        fact_text: str,
        category: str = "preference",
        importance: float = 0.95,
        entities: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Directly record an explicit fact or user preference into Memora.
        """
        payload = {
            "content_text": fact_text,
            "memory_type": "semantic",
            "source": f"agent:{agent_name.lower()}",
            "confidence": 1.0,
            "importance": importance,
            "provenance": {
                "category": category,
                "entities": entities or ["user_preference", category]
            }
        }

        url = f"{self.base_url}/v1/memories"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-Agent-Name": agent_name.lower()
        }

        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status in (200, 201):
                    return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"Memora API write failed ({e}), using local fallback.")

        return self._record_fact_locally(agent_name, fact_text, category, importance, entities)

    def recall_memories(
        self,
        agent_name: str,
        query: str,
        limit: int = 5,
        threshold: float = 0.2
    ) -> List[Dict[str, Any]]:
        """
        Recall relevant persistent memories for a task or conversation turn.
        """
        if not query or not query.strip():
            return []

        encoded_q = urllib.parse.quote(query.strip())
        url = f"{self.base_url}/v1/memories/search?q={encoded_q}&limit={limit}&min_score={threshold}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Agent-Name": agent_name.lower()
        }

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status == 200:
                    results = json.loads(response.read().decode("utf-8"))
                    if results:
                        return results
        except Exception as e:
            logger.debug(f"Memora API recall failed ({e}), searching local database.")

        return self._recall_locally(agent_name, query, limit)

    def build_context_prompt(self, agent_name: str, query: str, max_tokens: int = 800) -> str:
        """
        Build an authoritative prompt context block from recalled memories.
        """
        memories = self.recall_memories(agent_name, query, limit=5)
        if not memories:
            return ""

        lines = [
            "[PERSISTENT LONG-TERM MEMORY (MEMORA)]:",
            "The following verified facts and memories were retrieved from your persistent knowledge fabric:"
        ]
        
        seen = set()
        for m in memories:
            content = m.get("content_text", "").strip()
            if content and content not in seen:
                seen.add(content)
                mtype = m.get("memory_type", "memory").upper()
                lines.append(f"- [{mtype}] {content}")

        lines.append("Act on these facts naturally and accurately without asking the user to repeat themselves.")
        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Local SQLite Fallback Engine
    # -------------------------------------------------------------------------

    def _get_agent_and_ns_ids(self, conn: sqlite3.Connection, agent_name: str) -> tuple[str, str]:
        c = conn.cursor()
        c.execute("SELECT id FROM agents WHERE name = ?", (agent_name.lower(),))
        row = c.fetchone()
        if row:
            aid = row[0]
        else:
            aid = str(uuid.uuid4())
            c.execute("INSERT INTO agents (id, name, role, tenant_id) VALUES (?, ?, ?, ?)",
                      (aid, agent_name.lower(), "worker", "default"))
        
        c.execute("SELECT id FROM namespaces WHERE agent_id = ?", (aid,))
        row = c.fetchone()
        if row:
            nid = row[0]
        else:
            nid = str(uuid.uuid4())
            c.execute("INSERT INTO namespaces (id, path, type, agent_id, tenant_id) VALUES (?, ?, ?, ?, ?)",
                      (nid, f"memora://{agent_name.lower()}/private", "private", aid, "default"))
        conn.commit()
        return aid, nid

    def _record_locally(
        self,
        agent_name: str,
        user_input: str,
        agent_output: str,
        event_type: str,
        tags: Optional[List[str]],
        metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not os.path.exists(self.local_db_path):
            return {"status": "error", "message": "Local DB not found"}

        try:
            from core.memory.pipeline.preference_extractor import PreferenceExtractor
            extracted_facts = PreferenceExtractor.extract_facts(user_input)
        except Exception:
            extracted_facts = []

        now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
        created_ids = []

        try:
            with sqlite3.connect(self.local_db_path, timeout=5.0) as conn:
                aid, nid = self._get_agent_and_ns_ids(conn, agent_name)
                c = conn.cursor()

                # 1. Save extracted facts as SEMANTIC memories
                for fact in extracted_facts:
                    mid = str(uuid.uuid4())
                    c.execute("""
                        INSERT INTO memory_records 
                        (id, namespace_id, owner_id, memory_type, content_text, source, confidence, importance, lifecycle_state, tenant_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (mid, nid, aid, "semantic", fact.normalized_fact, f"agent:{agent_name.lower()}", fact.confidence, fact.importance, "active", "default", now_iso))
                    created_ids.append(mid)

                # 2. Save dialogue as EPISODIC memory
                mid_ep = str(uuid.uuid4())
                dialogue_text = f"User: {user_input} | Assistant: {agent_output}"
                c.execute("""
                    INSERT INTO memory_records 
                    (id, namespace_id, owner_id, memory_type, content_text, source, confidence, importance, lifecycle_state, tenant_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mid_ep, nid, aid, "episodic", dialogue_text, f"agent:{agent_name.lower()}", 1.0, 0.7, "active", "default", now_iso))
                created_ids.append(mid_ep)
                conn.commit()

            return {"status": "success", "memory_ids": created_ids, "facts_extracted": len(extracted_facts)}
        except Exception as e:
            logger.error(f"Local memory record failed: {e}")
            return {"status": "error", "message": str(e)}

    def _record_fact_locally(
        self,
        agent_name: str,
        fact_text: str,
        category: str,
        importance: float,
        entities: Optional[List[str]]
    ) -> Dict[str, Any]:
        if not os.path.exists(self.local_db_path):
            return {"status": "error", "message": "Local DB not found"}

        now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
        mid = str(uuid.uuid4())
        try:
            with sqlite3.connect(self.local_db_path, timeout=5.0) as conn:
                aid, nid = self._get_agent_and_ns_ids(conn, agent_name)
                c = conn.cursor()
                c.execute("""
                    INSERT INTO memory_records 
                    (id, namespace_id, owner_id, memory_type, content_text, source, confidence, importance, lifecycle_state, tenant_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mid, nid, aid, "semantic", fact_text, f"agent:{agent_name.lower()}", 1.0, importance, "active", "default", now_iso))
                conn.commit()
            return {"status": "success", "id": mid, "memory_type": "semantic"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _recall_locally(self, agent_name: str, query: str, limit: int) -> List[Dict[str, Any]]:
        if not os.path.exists(self.local_db_path):
            return []

        words = [w.lower() for w in query.split() if len(w) > 2]
        if not words:
            return []

        results = []
        try:
            with sqlite3.connect(self.local_db_path, timeout=5.0) as conn:
                c = conn.cursor()
                # Query memories across the ecosystem
                c.execute("""
                    SELECT m.id, m.content_text, m.memory_type, m.importance, m.created_at, a.name 
                    FROM memory_records m
                    JOIN agents a ON m.owner_id = a.id
                    WHERE m.lifecycle_state = 'active'
                    ORDER BY m.importance DESC, m.created_at DESC
                    LIMIT 100
                """)
                rows = c.fetchall()

                for row in rows:
                    content = row[1].lower()
                    # Calculate simple overlap score
                    match_count = sum(1 for w in words if w in content)
                    if match_count > 0:
                        score = match_count / len(words)
                        results.append({
                            "id": row[0],
                            "content_text": row[1],
                            "memory_type": row[2],
                            "importance": row[3],
                            "created_at": row[4],
                            "owner_name": row[5],
                            "final_score": score
                        })

            results.sort(key=lambda x: (x["final_score"], x["importance"]), reverse=True)
            return results[:limit]
        except Exception as e:
            logger.error(f"Local memory search failed: {e}")
            return []

# Singleton instance for quick access
memora_client = MemoraClient()
