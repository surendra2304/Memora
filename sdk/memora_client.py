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
            candidates = [
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memora.db"),
                "d:/FRIDAY Universe/Memora/data/memora.db",
                "d:\\FRIDAY Universe\\Memora\\data\\memora.db",
                "data/memora.db"
            ]
            self.local_db_path = next((c for c in candidates if os.path.exists(c)), candidates[0])

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

    def learn_from_outcome(
        self,
        agent_name: str,
        task_name: str,
        status: str,
        error_log: Optional[str] = None,
        actions_taken: Optional[str] = None,
        context: Optional[str] = None,
        domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Record a task outcome (success or failure) and synthesize operational guidelines
        into high-importance Experience memory so the agent upgrades its future decisions.
        """
        payload = {
            "agent_name": agent_name.lower(),
            "task_name": task_name,
            "status": status,
            "error_log": error_log,
            "actions_taken": actions_taken,
            "context": context,
            "domain": domain or "operational"
        }

        url = f"{self.base_url}/v1/memories/learn-outcome"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-Agent-Name": agent_name.lower()
        }

        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status in (200, 201):
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"Memora learn-outcome API write failed ({e}), using local fallback.")

        return self._learn_locally(agent_name, task_name, status, error_log, actions_taken, context, domain)

    def recall_experience(
        self,
        agent_name: str,
        task_query: str,
        domain: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant operational guidelines and experience memories for an agent.
        """
        encoded_domain = urllib.parse.quote(domain.strip()) if domain else ""
        url = f"{self.base_url}/v1/memories/experience?limit={limit}"
        if encoded_domain:
            url += f"&domain={encoded_domain}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Agent-Name": agent_name.lower()
        }

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    results = json.loads(resp.read().decode("utf-8"))
                    if results:
                        return results
        except Exception as e:
            logger.debug(f"Memora recall_experience API failed ({e}), using local fallback.")

        return self._recall_experience_locally(agent_name, task_query, domain, limit)

    def build_self_upgrade_context(
        self,
        agent_name: str,
        task_query: str,
        domain: Optional[str] = None
    ) -> str:
        """
        Synthesize an actionable self-upgrade instruction block from past learned lessons.
        """
        experiences = self.recall_experience(agent_name, task_query, domain=domain, limit=5)
        if not experiences:
            return ""

        lines = [
            "[SELF-UPGRADED OPERATIONAL GUIDELINES & EXPERIENCE (MEMORA)]:",
            "The following verified rules were learned from your past execution outcomes. Adapt your actions accordingly:"
        ]
        seen = set()
        for exp in experiences:
            txt = exp.get("content_text", "").strip()
            if txt and txt not in seen:
                seen.add(txt)
                lines.append(f"- {txt}")

        lines.append("Apply these operational rules to prevent past failures and ensure high execution quality.")
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

    @staticmethod
    def _extract_remediation_rule_fallback(task_name: str, error_log: str, domain: Optional[str] = None) -> str:
        err_lower = (error_log or "").lower()
        if "permission" in err_lower or "access denied" in err_lower or "elevat" in err_lower:
            return f"Verify process security privilege and administrator execution rights before running '{task_name}'."
        elif "not found" in err_lower or "no such file" in err_lower or "path" in err_lower:
            return f"Validate absolute filesystem paths and ensure target directory/file exists prior to executing '{task_name}'."
        elif "syntax" in err_lower or "unexpected token" in err_lower or "parse" in err_lower:
            return f"Ensure strict parameter quote escaping and schema validation before dispatching '{task_name}'."
        elif "timeout" in err_lower or "timed out" in err_lower or "deadline" in err_lower:
            return f"Increase request timeout budget and configure exponential backoff retries when calling '{task_name}'."
        elif "connection refused" in err_lower or "connect" in err_lower or "unreachable" in err_lower:
            return f"Check endpoint health status and verify socket/service availability before connecting in '{task_name}'."
        elif "rate limit" in err_lower or "429" in err_lower or "too many requests" in err_lower:
            return f"Apply rate limiter and automatically fallback to secondary provider gateway when running '{task_name}'."
        elif "import" in err_lower or "module" in err_lower or "dependency" in err_lower:
            return f"Inspect dependency environment and ensure required package is installed before launching '{task_name}'."
        elif "drawdown" in err_lower or "slippage" in err_lower or "volatil" in err_lower:
            return f"Reduce position sizing by 50% and enforce tighter stop-loss guardrails during high volatility in '{task_name}'."
        else:
            return f"Execute pre-flight parameter verification and handle graceful exceptions when executing '{task_name}'."

    def _learn_locally(
        self,
        agent_name: str,
        task_name: str,
        status: str,
        error_log: Optional[str] = None,
        actions_taken: Optional[str] = None,
        context: Optional[str] = None,
        domain: Optional[str] = None
    ) -> Dict[str, Any]:
        if not os.path.exists(self.local_db_path):
            return {"status": "error", "message": f"Local DB not found at {self.local_db_path}"}

        # Try to use ExperienceLearnerService if available, else fallback
        try:
            from core.memory.experience_service import ExperienceLearnerService
            rule = ExperienceLearnerService.extract_remediation_rule(task_name, error_log or "", domain)
        except Exception:
            rule = self._extract_remediation_rule_fallback(task_name, error_log or "", domain)

        dom = domain or "operational"
        if status.lower() in ("failure", "error", "crashed"):
            err_snippet = (error_log or "execution failure").strip().replace("\n", " ")[:150]
            content_text = f"[FAILURE WARNING in '{dom}'] Task: {task_name}. Trigger: {err_snippet}. [LEARNED BEST PRACTICE]: {rule}"
        else:
            cfg = (context or actions_taken or "standard baseline").strip().replace("\n", " ")[:150]
            content_text = f"[PROVEN SUCCESS PATTERN in '{dom}'] Task: {task_name}. Configuration '{cfg}' succeeded. Replicate this strategy."

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
                """, (mid, nid, aid, "experience", content_text, f"agent:{agent_name.lower()}", 1.0, 0.99, "active", "default", now_iso))
                conn.commit()
            return {"status": "success", "id": mid, "memory_type": "experience", "content": content_text, "rule": rule}
        except Exception as e:
            logger.error(f"Local learn-outcome record failed: {e}")
            return {"status": "error", "message": str(e)}

    def _recall_experience_locally(
        self,
        agent_name: str,
        task_query: str,
        domain: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        if not os.path.exists(self.local_db_path):
            return []

        results = []
        try:
            with sqlite3.connect(self.local_db_path, timeout=5.0) as conn:
                c = conn.cursor()
                query = """
                    SELECT m.id, m.content_text, m.memory_type, m.importance, m.created_at, a.name 
                    FROM memory_records m
                    JOIN agents a ON m.owner_id = a.id
                    WHERE m.lifecycle_state = 'active' AND m.memory_type = 'experience'
                """
                c.execute(query + " ORDER BY m.importance DESC, m.created_at DESC LIMIT 50")
                rows = c.fetchall()

                search_terms = [w.lower() for w in f"{task_query} {domain or ''}".split() if len(w) > 2]
                
                for row in rows:
                    content = row[1].lower()
                    match_count = sum(1 for w in search_terms if w in content) if search_terms else 1
                    score = (match_count / len(search_terms)) if search_terms else 1.0
                    if match_count > 0 or not search_terms:
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
            logger.error(f"Local experience recall failed: {e}")
            return []

# Singleton instance for quick access
memora_client = MemoraClient()
