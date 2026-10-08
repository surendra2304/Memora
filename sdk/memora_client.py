"""
Universal Memora Client SDK for FRIDAY Universe Agents
Provides fail-safe persistent memory recording, semantic fact extraction,
and context recall across all 9 autonomous subsystems.
"""
import os
import json
import hashlib
import hmac
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
    Sends all shared memory operations through the Memora API. API outages are
    returned to callers explicitly; local SQLite must never become an authority.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        local_db_path: Optional[str] = None,
        timeout: float = 4.0
    ):
        self.base_url = (base_url or os.getenv("MEMORA_URL", "https://memora-cavc.onrender.com")).rstrip("/")
        self.api_key = api_key or os.getenv("MEMORA_API_KEY")
        self.timeout = timeout
        
        # Legacy private helpers may still be used explicitly by offline tools,
        # but normal SDK operations never discover or fall back to a local DB.
        self.local_db_path = local_db_path or ""

    def _headers(self, agent_name: str, *, json_body: bool = False, api_key: Optional[str] = None) -> Dict[str, str]:
        agent = agent_name.lower().strip()
        headers = {"X-Agent-Name": agent}
        if json_body:
            headers["Content-Type"] = "application/json"
        # Agent routes authenticate the agent's own identity; MEMORA_API_KEY is
        # for service-to-service administration and must never impersonate an agent.
        credential = api_key or os.getenv(f"{agent.upper()}_API_KEY")
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return headers

    def poll_events(
        self,
        agent_name: str,
        after_id: int = 0,
        limit: int = 100,
        event_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch durable Memora notifications after a saved cursor.

        The caller owns cursor persistence. An unavailable service returns an
        explicit error object so a network failure cannot look like an empty feed.
        """
        if after_id < 0 or not 1 <= limit <= 500:
            return {"status": "error", "error": "after_id must be non-negative and limit must be 1..500"}
        params = {"after_id": after_id, "limit": limit}
        if event_type:
            params["event_type"] = event_type
        url = f"{self.base_url}/v1/events?{urllib.parse.urlencode(params)}"
        agent_key = os.getenv(f"{agent_name.upper()}_API_KEY")
        if not agent_key:
            return {"status": "error", "error": f"{agent_name.upper()}_API_KEY is not configured"}
        headers = self._headers(agent_name, api_key=agent_key)
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status != 200:
                    return {"status": "error", "error": f"Memora returned HTTP {response.status}"}
                result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict) or not isinstance(result.get("events"), list):
                    return {"status": "error", "error": "Memora returned an invalid event feed"}
                return {"status": "ok", **result}
        except Exception as exc:
            logger.debug("Memora event feed unavailable (%s)", type(exc).__name__)
            return {"status": "error", "error": type(exc).__name__}

    def read_event_cursor(self, agent_name: str, consumer_id: str = "default") -> Dict[str, Any]:
        """Read the server-persisted cursor owned only by this agent."""
        agent = agent_name.lower().strip()
        key = os.getenv(f"{agent.upper()}_API_KEY")
        if not key:
            return {"status": "error", "error": f"{agent.upper()}_API_KEY is not configured"}
        try:
            req = urllib.request.Request(
                f"{self.base_url}/v1/events/cursor?{urllib.parse.urlencode({'consumer_id': consumer_id})}",
                headers=self._headers(agent, api_key=key),
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                return {"status": "ok", **data} if response.status == 200 and isinstance(data, dict) else {"status": "error", "error": f"Memora returned HTTP {response.status}"}
        except Exception as exc:
            logger.warning("Memora cursor read failed (%s)", type(exc).__name__)
            return {"status": "error", "error": type(exc).__name__}

    def acknowledge_event(self, agent_name: str, event_id: int, consumer_id: str = "default") -> Dict[str, Any]:
        """Persist acknowledgement only after the caller has handled an event."""
        agent = agent_name.lower().strip()
        key = os.getenv(f"{agent.upper()}_API_KEY")
        if not key:
            return {"status": "error", "error": f"{agent.upper()}_API_KEY is not configured"}
        try:
            req = urllib.request.Request(
                f"{self.base_url}/v1/events/ack",
                data=json.dumps({"event_id": int(event_id), "consumer_id": consumer_id}).encode("utf-8"),
                headers=self._headers(agent, json_body=True, api_key=key),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                return {"status": "ok", **data} if response.status == 200 and isinstance(data, dict) else {"status": "error", "error": f"Memora returned HTTP {response.status}"}
        except Exception as exc:
            logger.warning("Memora event acknowledgement failed (%s)", type(exc).__name__)
            return {"status": "error", "error": type(exc).__name__}

    def publish_event(
        self,
        source_agent: str,
        target_agent: str,
        intent: str,
        payload: Dict[str, Any],
        *,
        priority: str = "normal",
        ttl: int = 300,
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sign and persist one idempotent agent notification through Memora."""
        source = source_agent.lower().strip()
        key = os.getenv(f"{source.upper()}_API_KEY")
        if not key:
            return {"status": "error", "error": f"{source.upper()}_API_KEY is not configured"}
        if not (1 <= ttl <= 86400):
            return {"status": "error", "error": "ttl must be in the range 1..86400 seconds"}
        envelope = {
            "message_id": message_id or f"msg_{uuid.uuid4().hex}",
            "correlation_id": f"corr_{uuid.uuid4().hex[:16]}",
            "from_agent": source,
            "to_agent": target_agent.lower().strip(),
            "intent": intent,
            "priority": priority,
            "ttl": ttl,
            "auth_token": None,
            "payload": payload,
            "created_at": time.time(),
        }
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str).encode()
        envelope["signature"] = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
        headers = self._headers(source, json_body=True, api_key=key)
        try:
            req = urllib.request.Request(
                f"{self.base_url}/mesh/envelope",
                data=json.dumps(envelope).encode(),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return {"status": "ok", **result} if response.status == 202 else {"status": "error", "error": f"HTTP {response.status}"}
        except Exception as exc:
            logger.debug("Memora event publish failed (%s)", type(exc).__name__)
            return {"status": "error", "error": type(exc).__name__}

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
        headers = self._headers(agent_name, json_body=True)
        if not headers.get("Authorization"):
            return {"status": "error", "error": f"{agent_name.upper()}_API_KEY is not configured", "cloud": False}

        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status in (200, 201):
                    return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            logger.warning("Memora interaction write failed (%s)", type(e).__name__)
            return {"status": "error", "error": type(e).__name__, "cloud": False}
        return {"status": "error", "error": "Memora returned an unexpected response", "cloud": False}

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
            "memory_type": "episodic",
            "source": f"agent:{agent_name.lower()}",
            "confidence": 1.0,
            "importance": importance,
            "provenance": {
                "category": category,
                "entities": entities or ["user_preference", category]
            }
        }

        url = f"{self.base_url}/v1/memories"
        headers = self._headers(agent_name, json_body=True)
        if not headers.get("Authorization"):
            return {"status": "error", "error": f"{agent_name.upper()}_API_KEY is not configured", "cloud": False}

        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status in (200, 201):
                    return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            logger.warning("Memora fact write failed (%s)", type(e).__name__)
            return {"status": "error", "error": type(e).__name__, "cloud": False}
        return {"status": "error", "error": "Memora returned an unexpected response", "cloud": False}

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
        headers = self._headers(agent_name)
        if not headers.get("Authorization"):
            return []

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status == 200:
                    results = json.loads(response.read().decode("utf-8"))
                    if results:
                        return results
        except Exception as e:
            logger.warning("Memora recall failed (%s)", type(e).__name__)
            return []
        return []

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
        headers = self._headers(agent_name, json_body=True)
        if not headers.get("Authorization"):
            return {"status": "error", "error": f"{agent_name.upper()}_API_KEY is not configured", "cloud": False}

        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status in (200, 201):
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning("Memora outcome write failed (%s)", type(e).__name__)
            return {"status": "error", "error": type(e).__name__, "cloud": False}
        return {"status": "error", "error": "Memora returned an unexpected response", "cloud": False}

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

        headers = self._headers(agent_name)
        if not headers.get("Authorization"):
            return []

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    results = json.loads(resp.read().decode("utf-8"))
                    if results:
                        return results
        except Exception as e:
            logger.warning("Memora experience recall failed (%s)", type(e).__name__)
            return []
        return []

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
        c.execute("SELECT id FROM agents WHERE name = ? AND tenant_id = 'default'", (agent_name.lower(),))
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

                # 1. Save unverified extracted facts as EPISODIC candidates.
                from core.memory.pipeline.secret_scanner import SecretScanner
                from core.memory.pipeline.poison_detector import PoisonDetector

                for fact in extracted_facts:
                    SecretScanner.validate_content_safety(fact.normalized_fact)
                    PoisonDetector.validate_content_safety(fact.normalized_fact)
                    mid = str(uuid.uuid4())
                    c.execute("""
                        INSERT INTO memory_records 
                        (id, namespace_id, owner_id, memory_type, content_text, source, confidence, importance, lifecycle_state, tenant_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (mid, nid, aid, "episodic", fact.normalized_fact, f"agent:{agent_name.lower()}", fact.confidence, fact.importance, "active", "default", now_iso))
                    created_ids.append(mid)

                # 2. Save dialogue as EPISODIC memory after the same safety checks.
                dialogue_text = f"User: {user_input} | Assistant: {agent_output}"
                SecretScanner.validate_content_safety(dialogue_text)
                PoisonDetector.validate_content_safety(dialogue_text)
                mid_ep = str(uuid.uuid4())
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
            from core.memory.pipeline.secret_scanner import SecretScanner
            from core.memory.pipeline.poison_detector import PoisonDetector

            SecretScanner.validate_content_safety(fact_text)
            PoisonDetector.validate_content_safety(fact_text)
            with sqlite3.connect(self.local_db_path, timeout=5.0) as conn:
                aid, nid = self._get_agent_and_ns_ids(conn, agent_name)
                c = conn.cursor()
                c.execute("""
                    INSERT INTO memory_records 
                    (id, namespace_id, owner_id, memory_type, content_text, source, confidence, importance, lifecycle_state, tenant_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (mid, nid, aid, "episodic", fact_text, f"agent:{agent_name.lower()}", 1.0, importance, "candidate", "default", now_iso))
                conn.commit()
            return {"status": "success", "id": mid, "memory_type": "episodic"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _recall_locally(self, agent_name: str, query: str, limit: int) -> List[Dict[str, Any]]:
        if not os.path.exists(self.local_db_path):
            return []

        import re
        tokens = [w for w in re.findall(r'\b\w+\b', query.lower()) if len(w) > 1]
        stopwords = {"what", "who", "where", "when", "why", "how", "is", "are", "am", "was", "were", "my", "your", "his", "her", "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or"}
        content_words = [w for w in tokens if w not in stopwords]
        search_words = content_words if content_words else tokens
        if not search_words:
            return []

        results = []
        try:
            with sqlite3.connect(self.local_db_path, timeout=5.0) as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT id FROM agents WHERE name = ? AND tenant_id = 'default'",
                    (agent_name.lower(),),
                )
                actor_row = c.fetchone()
                if actor_row is None:
                    return []

                like_clauses = " OR ".join(["m.content_text LIKE ?" for _ in search_words])
                params = [actor_row[0], *[f"%{w}%" for w in search_words]]

                c.execute(f"""
                    SELECT m.id, m.content_text, m.memory_type, m.importance, m.created_at, a.name
                    FROM memory_records m
                    JOIN agents a ON m.owner_id = a.id
                    JOIN namespaces n ON m.namespace_id = n.id
                    WHERE m.tenant_id = 'default'
                      AND (m.owner_id = ?
                           OR n.path LIKE 'memora://universe/%'
                           OR n.path LIKE 'memora://public/%')
                      AND m.lifecycle_state = 'active'
                      AND ({like_clauses})
                    ORDER BY m.importance DESC, m.created_at DESC
                    LIMIT 200
                """, params)
                rows = c.fetchall()

                for row in rows:
                    content = row[1].lower()
                    match_count = sum(1 for w in search_words if w in content)
                    if match_count > 0:
                        score = match_count / len(search_words)
                        results.append({
                            "id": row[0],
                            "content_text": row[1],
                            "memory_type": row[2],
                            "importance": row[3],
                            "created_at": row[4],
                            "owner_name": row[5],
                            "final_score": score
                        })

            results.sort(key=lambda x: (x["final_score"], x["importance"], x["created_at"]), reverse=True)
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
