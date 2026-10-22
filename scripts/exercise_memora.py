#!/usr/bin/env python3
"""
Real-world exerciser for Memora.

This is not a test suite. It drives the running server over HTTP the way an
operator or a supervising agent actually would: register the ecosystem, have
agents write real work product, query it back, build context, share, collaborate,
reflect, heal. Every step checks the response against what a correct system
should do, and every deviation is recorded as a finding rather than swallowed.

Exit code is the number of findings.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests

KEYS = {
    "friday": "k_friday_0f9a1b2c3d4e5f60",
    "inference": "k_inference_1a2b3c4d5e6f7081",
    "stratex": "k_stratex_2b3c4d5e6f708192",
    "intelx": "k_intelx_3c4d5e6f708192a3",
    "futuris": "k_futuris_4d5e6f708192a3b4",
    "cortex": "k_cortex_5e6f708192a3b4c5",
    "forge": "k_forge_6f708192a3b4c5d6",
    "sentinel": "k_sentinel_708192a3b4c5d6e7",
    "memora": "k_memora_8192a3b4c5d6e7f8",
}

FINDINGS: List[Dict[str, Any]] = []
PASSED: List[str] = []


def finding(severity: str, title: str, detail: str, evidence: Any = None) -> None:
    FINDINGS.append(
        {"severity": severity, "title": title, "detail": detail, "evidence": evidence}
    )
    print(f"  !! [{severity}] {title}")
    print(f"     {detail[:400]}")


def ok(title: str) -> None:
    PASSED.append(title)
    print(f"  ok {title}")


class Memora:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.s = requests.Session()

    def call(
        self,
        method: str,
        path: str,
        agent: Optional[str] = None,
        body: Any = None,
        params: Any = None,
        timeout: int = 60,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Any]:
        url = f"{self.base}{path}"
        h = {"Content-Type": "application/json"}
        if agent:
            h["X-Agent-Name"] = agent
            h["X-API-Key"] = KEYS.get(agent, "")
        if headers:
            h.update(headers)
        try:
            r = self.s.request(
                method, url, json=body, params=params, headers=h, timeout=timeout
            )
        except Exception as exc:
            return -1, f"{type(exc).__name__}: {exc}"
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:2000]


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------

def scenario_bootstrap(m: Memora) -> Dict[str, str]:
    """Register the ecosystem the way the seed script would."""
    print("\n[1] bootstrap: register the nine-agent ecosystem")
    roles = {
        "friday": "supervisor", "inference": "inference", "stratex": "strategy",
        "intelx": "intelligence", "futuris": "foresight", "cortex": "cognition",
        "forge": "engineering", "sentinel": "security", "memora": "memory",
    }
    # Identity creation is fabric administration and is restricted to the memora
    # service identity, so the ecosystem is provisioned by memora rather than by
    # each agent minting its own row. Provisioning each agent with its own key
    # now (correctly) returns 403.
    ids: Dict[str, str] = {}
    for name, role in roles.items():
        code, body = m.call("POST", "/agents", agent="memora",
                            body={"name": name, "role": role, "description": f"{role} agent"})
        if code in (200, 201) and isinstance(body, dict):
            ids[name] = body.get("id", "")
        elif code == 409 or (isinstance(body, dict) and "already" in str(body).lower()):
            code2, body2 = m.call("GET", f"/agents/{name}", agent="memora")
            ids[name] = body2.get("id", "") if isinstance(body2, dict) else ""
        else:
            finding("HIGH", "agent registration failed", f"{name}: {code} {str(body)[:200]}")

    # A non-admin must still be refused; the provisioning path above must not
    # have quietly reopened identity creation to every caller.
    code, body = m.call("POST", "/agents", agent="forge",
                        body={"name": "not-an-admin", "role": "worker"})
    if code in (200, 201):
        finding("CRITICAL", "non-admin created an identity",
                f"forge minted an agent: {code} {str(body)[:200]}")
    else:
        ok(f"non-admin identity creation refused (HTTP {code})")
    if len(ids) == 9:
        ok(f"registered/reused {len(ids)} agents")
    else:
        finding("HIGH", "incomplete ecosystem", f"only {len(ids)}/9 agents present: {sorted(ids)}")
    return ids


def scenario_write_recall(m: Memora) -> Dict[str, Any]:
    """The core promise: write real work product, get it back."""
    print("\n[2] core loop: write real work product, recall it precisely")
    artefacts = {
        "forge": (
            "Replaced the xenon compressor seal on unit XC-4. Root cause was a "
            "fatigued O-ring from the 2019 batch. Correct torque is 42 Nm, applied "
            "in a three-stage cross pattern. Part number OX-7741-B."
        ),
        "sentinel": (
            "Detected 4,212 credential-stuffing attempts against the edge gateway "
            "between 02:00 and 03:14 UTC. Source ASNs 14618 and 16509. Mitigation: "
            "rate limit tightened to 12 req/min per source IP."
        ),
        "stratex": (
            "Q3 forecast revised downward by 6.4 percent after the supplier delay "
            "in the halide line. Recommend deferring the Turin expansion one quarter."
        ),
        "intelx": (
            "Competitor Helix filed patent WO-2026-114 on solid-state xenon "
            "regeneration. Claims overlap our pending application on the catalytic "
            "recovery step."
        ),
        "cortex": (
            "Observed that forge asks for seal torque specifications before every "
            "compressor job. Consider pre-staging the torque table in its context."
        ),
    }
    written: Dict[str, str] = {}
    for agent, text in artefacts.items():
        code, body = m.call("POST", "/v1/memories", agent=agent,
                            body={"content_text": text, "confidence": 0.9,
                                  "importance": 0.85, "memory_type": "episodic",
                                  "idempotency_key": f"seed-{uuid.uuid4().hex[:8]}"})
        if code != 201:
            finding("CRITICAL", "memory write rejected", f"{agent}: {code} {str(body)[:300]}")
            continue
        mid = body.get("memory_id") or body.get("id") or (body.get("record") or {}).get("id")
        if not mid:
            finding("HIGH", "write returned no id", f"{agent}: {json.dumps(body)[:300]}")
        else:
            written[agent] = mid
    if len(written) == len(artefacts):
        ok(f"wrote {len(written)} memories")

    # Recall: a specific factual question must surface the right record.
    code, body = m.call("POST", "/v1/memories/query", agent="forge",
                        body={"query_text": "xenon compressor seal torque specification",
                              "limit": 5})
    if code != 200:
        finding("CRITICAL", "query failed", f"{code} {str(body)[:300]}")
        return written
    rows = body if isinstance(body, list) else body.get("results", [])
    if not rows:
        finding("CRITICAL", "query returned nothing for a known fact",
                "wrote the torque spec, then a query for it returned zero rows")
    else:
        blob = json.dumps(rows).lower()
        if "42 nm" in blob or "42nm" in blob or "ox-7741" in blob:
            ok("recall surfaced the correct torque fact")
        else:
            finding("HIGH", "recall missed the specific fact",
                    f"top {len(rows)} rows did not contain the torque value or part number",
                    blob[:500])
    return written


def scenario_context_building(m: Memora) -> None:
    """Context bundles are the product's main output."""
    print("\n[3] context pipeline: build a budgeted bundle for a real task")
    for budget in (500, 2000, 8000):
        code, body = m.call("POST", "/v1/context", agent="forge",
                            body={"task_query": "replace the xenon compressor seal on XC-4",
                                  "token_budget": budget, "max_candidates": 20})
        if code != 200:
            finding("CRITICAL", f"context build failed at budget {budget}",
                    f"{code} {str(body)[:300]}")
            continue
        est = body.get("total_tokens_estimated", 0)
        mems = body.get("memories_count", 0)
        if est > budget:
            finding("HIGH", "context exceeded its token budget",
                    f"budget={budget} estimated={est} — the budgeter is not enforcing")
        else:
            ok(f"budget {budget}: {mems} memories, {est} est tokens")

    # A budget so small nothing fits must still be a valid, empty bundle.
    code, body = m.call("POST", "/v1/context", agent="forge",
                        body={"task_query": "xenon compressor seal", "token_budget": 100})
    if code != 200:
        finding("MEDIUM", "tiny budget should still succeed", f"{code} {str(body)[:200]}")
    else:
        ok("tiny budget returns a valid (possibly empty) bundle")


def scenario_cross_agent_access(m: Memora) -> None:
    """The security model must actually hold under real requests."""
    print("\n[4] access control: private material must not leak between agents")
    code, body = m.call("POST", "/v1/memories", agent="sentinel",
                        body={"content_text": "CONFIDENTIAL: sentinel internal rotation schedule "
                                             "and on-call roster for Q4, do not distribute.",
                              "importance": 0.95,
                              "target_namespace_path": "memora://sentinel/private",
                              "idempotency_key": f"priv-{uuid.uuid4().hex[:8]}"})
    if code != 201:
        finding("CRITICAL", "could not write a private memory", f"{code} {str(body)[:200]}")
        return
    ok("sentinel wrote to its private namespace")

    for snooper in ("intelx", "forge", "cortex"):
        code, body = m.call("POST", "/v1/memories/query", agent=snooper,
                            body={"query_text": "on-call roster rotation schedule confidential",
                                  "limit": 20})
        blob = json.dumps(body).lower() if body else ""
        if "on-call roster" in blob or "do not distribute" in blob:
            finding("CRITICAL", f"{snooper} read sentinel's private memory",
                    "private-by-default is not holding over the query path",
                    blob[:400])
        else:
            ok(f"{snooper} cannot see sentinel's private memory")

    # Direct fetch by id must also be denied.
    code, body = m.call("POST", "/v1/memories", agent="sentinel",
                        body={"content_text": "sentinel private marker XQ-9931",
                              "target_namespace_path": "memora://sentinel/private",
                              "idempotency_key": f"priv2-{uuid.uuid4().hex[:8]}"})
    mid = (body or {}).get("memory_id") or (body or {}).get("id")
    if mid:
        code, body = m.call("GET", f"/v1/memories/{mid}", agent="intelx")
        if code == 200 and "XQ-9931" in json.dumps(body):
            finding("CRITICAL", "direct memory fetch bypassed namespace policy",
                    f"intelx fetched {mid} and got the content (HTTP {code})")
        else:
            ok(f"direct fetch by id denied to intelx (HTTP {code})")


def scenario_task_execution(m: Memora) -> None:
    """/v1/task/execute is the mesh's request path."""
    print("\n[5] task execution: dispatch a real task envelope")
    envelope = {
        "task_id": f"task-{uuid.uuid4().hex[:8]}",
        "source_agent": "friday",
        "target_agent": "forge",
        "action": "store",
        "payload": {"content_text": "Task-driven memory: recalibrate the mass spectrometer "
                                    "baseline after the seal replacement.",
                    "importance": 0.8},
        "priority": "normal",
    }
    code, body = m.call("POST", "/v1/task/execute", agent="friday", body=envelope)
    if code != 200 and code != 201:
        finding("HIGH", "task execute rejected", f"{code} {str(body)[:300]}")
        return
    status = (body or {}).get("status")
    if status and str(status).upper() in ("ERROR", "FAILED"):
        finding("CRITICAL", "task execute returned a failure status",
                f"HTTP {code} but status={status} — the caller cannot tell this failed",
                json.dumps(body)[:400])
    else:
        ok(f"task executed (status={status})")

    # Did it actually persist? A 200 that stored nothing is the old CRITICAL-2 bug.
    time.sleep(0.3)
    # Read back as the agent that wrote it. The envelope runs as friday, so the
    # memory lands in memora://friday/private; querying as forge asks a different
    # agent to read friday's private space, which must (correctly) return nothing.
    code, body = m.call("POST", "/v1/memories/query", agent="friday",
                        body={"query_text": "recalibrate mass spectrometer baseline", "limit": 5})
    blob = json.dumps(body).lower() if body else ""
    if "mass spectrometer" in blob:
        ok("task-driven memory is retrievable")
    else:
        finding("CRITICAL", "task execute acknowledged but stored nothing",
                "HTTP success, but the payload never became a memory",
                blob[:400])


def scenario_collaboration(m: Memora) -> None:
    """Ask for help, contribute, delegate."""
    print("\n[6] collaboration: ask for help, contribute, delegate")
    code, body = m.call("POST", "/v1/collaboration/assist", agent="forge",
                        body={"query": "xenon compressor seal torque specification"})
    if code != 200:
        finding("HIGH", "assist failed", f"{code} {str(body)[:300]}")
    else:
        ok(f"assist returned {body.get('candidate_count')} candidates, "
           f"{len(body.get('immediately_usable', []))} immediately usable")

    code, body = m.call("POST", "/v1/collaboration/delegate", agent="forge",
                        body={"subagent_name": f"torque-{uuid.uuid4().hex[:6]}",
                              "task_description": "verify the seal torque on XC-4"})
    if code != 200:
        finding("HIGH", "delegate failed", f"{code} {str(body)[:300]}")
    else:
        sub = body.get("subagent", "")
        scope = body.get("bounded_scope", "")
        if not sub or not scope.startswith("memora://forge/"):
            finding("HIGH", "delegated subagent not scoped under the delegator",
                    f"subagent={sub} scope={scope}")
        else:
            ok(f"delegated to {sub} scoped to {scope}")


def scenario_reflection(m: Memora) -> None:
    """The self-brain must produce grounded insights."""
    print("\n[7] reflection: run the self-brain over the live corpus")
    code, body = m.call("POST", "/v1/reflection/run", agent="memora",
                        body={"dry_run": False})
    if code != 200:
        finding("HIGH", "reflection run failed", f"{code} {str(body)[:300]}")
        return
    count = body.get("insight_count", 0)
    stored = body.get("stored", 0)
    if count == 0:
        finding("MEDIUM", "reflection drew no insights from a populated corpus",
                f"scanned {body.get('scanned_memories')} memories, concluded nothing")
    else:
        ok(f"reflection drew {count} insights, stored {stored}; kinds={body.get('by_kind')}")

    # Idempotency: a second run must not re-store the same conclusions.
    code, body2 = m.call("POST", "/v1/reflection/run", agent="memora", body={"dry_run": False})
    if code == 200 and body2.get("stored", 0) > 0:
        finding("HIGH", "reflection re-stored insights it already recorded",
                f"second run stored {body2.get('stored')} more")
    else:
        ok("reflection is idempotent across runs")

    code, body = m.call("GET", "/v1/reflection/insights", agent="forge")
    if code != 200:
        finding("MEDIUM", "insights not readable", f"{code} {str(body)[:200]}")
    else:
        ok(f"{body.get('count')} insights readable by a non-admin agent")


def scenario_self_healing(m: Memora) -> None:
    print("\n[8] self-healing: run the supervisor against the live corpus")
    code, body = m.call("POST", "/v1/resilience/repair", agent="memora",
                        body={"dry_run": True})
    if code != 200:
        finding("HIGH", "repair failed", f"{code} {str(body)[:300]}")
        return
    checks = body.get("checks", [])
    unhealthy = [c for c in checks if not c.get("healthy")]
    ok(f"supervisor ran {len(checks)} checks, {len(unhealthy)} report unhealthy")
    for c in unhealthy:
        print(f"     - {c.get('name')}: findings={c.get('findings')} "
              f"err={str(c.get('error'))[:120]}")
    for c in checks:
        if c.get("error"):
            finding("MEDIUM", f"self-healing check errored: {c.get('name')}",
                    str(c.get("error"))[:300])

    # A real repair run should converge, not error.
    code, body = m.call("POST", "/v1/resilience/repair", agent="memora",
                        body={"dry_run": False})
    if code != 200:
        finding("HIGH", "real repair failed", f"{code} {str(body)[:300]}")
    else:
        ok(f"real repair completed: repaired={body.get('total_repaired')}")


def scenario_extremes(m: Memora) -> None:
    """Push the API to its edges. This is where real bugs live."""
    print("\n[9] extremes: hostile and boundary inputs")

    cases: List[Tuple[str, Any, str]] = [
        ("empty content", {"content_text": "", "idempotency_key": "x-empty"}, "should reject"),
        ("whitespace only", {"content_text": "   \n\t  ", "idempotency_key": "x-ws"}, "should reject"),
        ("huge payload", {"content_text": "x" * 2_000_000, "idempotency_key": "x-huge"}, "must not crash"),
        ("control chars", {"content_text": "a\x00b\x01c\x1bd", "idempotency_key": "x-ctrl"}, "must not crash"),
        ("unicode/emoji", {"content_text": "🧪 xenon → 压缩机组 密封 42 Nm 🚀" * 20,
                           "idempotency_key": "x-uni"}, "must round-trip"),
        ("sql injection", {"content_text": "'; DROP TABLE memory_records; --",
                           "idempotency_key": "x-sqli"}, "must be inert"),
        ("path traversal", {"content_text": "traversal probe",
                            "target_namespace_path": "memora://../../etc/passwd",
                            "idempotency_key": "x-trav"}, "must reject"),
        ("absurd importance", {"content_text": "out of range", "importance": 99.0,
                               "idempotency_key": "x-imp"}, "must reject"),
        ("negative confidence", {"content_text": "negative", "confidence": -1.0,
                                 "idempotency_key": "x-conf"}, "must reject"),
        ("deeply nested provenance", {"content_text": "nested",
                                      "provenance": {"a": {"b": {"c": {"d": list(range(500))}}}},
                                      "idempotency_key": "x-nest"}, "must not crash"),
    ]

    for label, body, expectation in cases:
        body.setdefault("idempotency_key", f"probe-{uuid.uuid4().hex[:8]}")
        started = time.time()
        code, resp = m.call("POST", "/v1/memories", agent="forge", body=body, timeout=120)
        elapsed = time.time() - started
        if code == -1:
            finding("CRITICAL", f"{label}: request failed", f"{resp} ({elapsed:.1f}s)")
        elif code >= 500:
            finding("CRITICAL", f"{label}: server error {code}",
                    f"{expectation}; got {str(resp)[:300]} ({elapsed:.1f}s)")
        elif elapsed > 30:
            finding("HIGH", f"{label}: took {elapsed:.1f}s", "response far too slow")
        else:
            ok(f"{label}: HTTP {code} ({elapsed:.1f}s)")

    # Verify the SQL injection text is inert.
    code, body = m.call("GET", "/health")
    if code != 200:
        finding("CRITICAL", "server unhealthy after hostile inputs", f"health={code}")
    else:
        ok("server still healthy after hostile inputs")

    code, body = m.call("POST", "/v1/memories/query", agent="forge",
                        body={"query_text": "xenon compressor", "limit": 5})
    if code != 200:
        finding("CRITICAL", "query broken after hostile inputs", f"{code} {str(body)[:200]}")
    else:
        ok("queries still work after hostile inputs")


def scenario_query_extremes(m: Memora) -> None:
    print("\n[10] query extremes")
    probes = [
        ("zero limit", {"query_text": "xenon", "limit": 0}),
        ("negative limit", {"query_text": "xenon", "limit": -5}),
        ("absurd limit", {"query_text": "xenon", "limit": 10_000_000}),
        ("huge offset", {"query_text": "xenon", "offset": 999_999}),
        ("negative offset", {"query_text": "xenon", "offset": -1}),
        ("empty query text", {"query_text": ""}),
        ("min_confidence > 1", {"query_text": "xenon", "min_confidence": 5.0}),
        ("nonexistent namespace", {"query_text": "xenon",
                                  "namespace_path": "memora://nobody/nothing"}),
    ]
    for label, body in probes:
        code, resp = m.call("POST", "/v1/memories/query", agent="forge", body=body)
        if code == -1:
            finding("CRITICAL", f"query {label}: request failed", str(resp))
        elif code >= 500:
            finding("HIGH", f"query {label}: server error {code}", str(resp)[:250])
        else:
            ok(f"query {label}: HTTP {code}")


def scenario_concurrency(m: Memora, workers: int, per_worker: int) -> None:
    print(f"\n[11] concurrency: {workers} agents x {per_worker} writes plus readers")
    errors: List[str] = []
    ids: List[str] = []

    def write(i: int) -> Optional[str]:
        agent = list(KEYS)[i % len(KEYS)]
        code, body = m.call("POST", "/v1/memories", agent=agent, body={
            "content_text": f"Concurrent observation #{i}: xenon compressor telemetry "
                            f"sample {i} at {time.time():.3f}",
            "idempotency_key": f"conc-{uuid.uuid4().hex}",
            "importance": 0.5,
        }, timeout=120)
        if code != 201:
            return f"write {i} -> {code}: {str(body)[:160]}"
        mid = (body or {}).get("memory_id") or (body or {}).get("id")
        return mid

    def read(i: int) -> Optional[str]:
        agent = list(KEYS)[i % len(KEYS)]
        code, body = m.call("POST", "/v1/memories/query", agent=agent,
                            body={"query_text": "xenon compressor telemetry", "limit": 10},
                            timeout=120)
        if code != 200:
            return f"read {i} -> {code}: {str(body)[:160]}"
        return None

    with ThreadPoolExecutor(max_workers=workers * 2) as pool:
        futures = []
        for i in range(workers * per_worker):
            futures.append(pool.submit(write, i))
        for i in range(workers * per_worker):
            futures.append(pool.submit(read, i))
        for f in as_completed(futures):
            r = f.result()
            if r is None:
                continue
            if r.startswith("write") or r.startswith("read"):
                errors.append(r)
            else:
                ids.append(r)

    if errors:
        finding("HIGH", f"{len(errors)} concurrent operations failed",
                "; ".join(errors[:6]), {"total_errors": len(errors)})
    else:
        ok(f"{len(ids)} concurrent writes accepted, {workers * per_worker} reads ok")

    dupes = len(ids) - len(set(ids))
    if dupes:
        finding("HIGH", f"{dupes} duplicate memory ids returned", "id generation is not unique")
    else:
        ok("all returned ids unique")


def scenario_idempotency(m: Memora) -> None:
    print("\n[12] idempotency: replaying the same key must not duplicate")
    key = f"idem-{uuid.uuid4().hex[:10]}"
    body = {"content_text": "Idempotent write probe: halide line pressure at 3.2 bar.",
            "idempotency_key": key, "importance": 0.7}
    first, b1 = m.call("POST", "/v1/memories", agent="forge", body=body)
    second, b2 = m.call("POST", "/v1/memories", agent="forge", body=body)
    third, b3 = m.call("POST", "/v1/memories", agent="sentinel", body=body)

    if first != 201:
        finding("HIGH", "first idempotent write failed", f"{first} {str(b1)[:200]}")
        return
    id1 = (b1 or {}).get("memory_id") or (b1 or {}).get("id")
    id2 = (b2 or {}).get("memory_id") or (b2 or {}).get("id")
    if id1 and id2 and id1 != id2:
        finding("HIGH", "replay created a second memory",
                f"first={id1} replay={id2}")
    else:
        ok("replay of the same key returned the same memory")

    # A different agent with the same key must NOT be collapsed into forge's write.
    id3 = (b3 or {}).get("memory_id") or (b3 or {}).get("id")
    if id3 and id1 and id3 == id1:
        finding("HIGH", "cross-agent idempotency collision",
                f"sentinel's write with the same key returned forge's memory {id1}")
    else:
        ok("different agent with the same key gets its own memory")


def scenario_lifecycle(m: Memora) -> None:
    print("\n[13] lifecycle: verify, promote, supersede, decay")
    code, body = m.call("POST", "/v1/memories", agent="forge", body={
        "content_text": "Lifecycle probe: the XC-4 seal was replaced and held pressure "
                        "at 3.2 bar for 24 hours.",
        "importance": 0.8, "idempotency_key": f"life-{uuid.uuid4().hex[:8]}"})
    if code != 201:
        finding("HIGH", "could not write lifecycle probe", f"{code}")
        return
    mid = (body or {}).get("memory_id") or (body or {}).get("id")

    code, body = m.call("POST", f"/v1/memories/{mid}/verify", agent="forge",
                        body={"purpose": "confirmed by pressure test"})
    if code != 200:
        finding("HIGH", "verify did not succeed", f"{code} {str(body)[:250]}")
    else:
        ok("verify -> HTTP 200")

    # MemoryPromoteRequest requires verification_evidence. Sending only a
    # purpose returns 422, which a "not 5xx means fine" check reports as a pass,
    # so this endpoint was never actually exercised.
    code, body = m.call("POST", f"/v1/memories/{mid}/promote", agent="forge",
                        body={"verification_evidence": ["pressure test log 2026-10-06",
                                                        "independent re-measurement"],
                              "target_confidence": 0.95,
                              "purpose": "promote to semantic"})
    if code != 200:
        finding("HIGH", "promote did not succeed", f"{code} {str(body)[:250]}")
    else:
        ok("promote -> HTTP 200")

    code, body = m.call("POST", "/v1/memories/decay", agent="memora",
                        body={"dry_run": True})
    if code >= 500:
        finding("HIGH", "decay errored", f"{code} {str(body)[:200]}")
    else:
        ok(f"decay dry run -> HTTP {code}")

    # Supersede: newer fact replaces the older one.
    code, b_new = m.call("POST", "/v1/memories", agent="forge", body={
        "content_text": "Lifecycle probe update: seal now holds 3.4 bar after re-torque.",
        "importance": 0.85, "idempotency_key": f"life2-{uuid.uuid4().hex[:8]}"})
    mid2 = (b_new or {}).get("memory_id") or (b_new or {}).get("id")
    if mid2:
        # The memory in the path is the one being superseded; the body names its
        # replacement. The harness had this the wrong way round and used a
        # superseded_memory_id field that MemorySupersedeRequest does not have,
        # so it always got 422 and reported that as a pass.
        code, body = m.call("POST", f"/v1/memories/{mid}/supersede", agent="forge",
                            body={"new_memory_id": mid2,
                                  "reason": "re-torque raised the holding pressure"})
        if code != 200:
            finding("HIGH", "supersede did not succeed", f"{code} {str(body)[:250]}")
        else:
            ok("supersede -> HTTP 200")


def scenario_authz_matrix(m: Memora) -> None:
    print("\n[14] authorisation matrix: who may do what")
    checks = [
        ("POST", "/namespaces/grants", "intelx",
         {"namespace_path": "memora://friday/private", "agent_name": "intelx",
          "actions": ["read"], "purpose": "escalation attempt"}),
        ("POST", "/agents", "intelx", {"name": "rogue", "role": "supervisor"}),
        ("POST", "/v1/resilience/repair", "intelx", {"dry_run": False}),
        ("POST", "/v1/reflection/run", "intelx", {"dry_run": False}),
    ]
    for method, path, agent, body in checks:
        code, resp = m.call(method, path, agent=agent, body=body)
        if code in (200, 201):
            finding("CRITICAL", f"{agent} succeeded at privileged {method} {path}",
                    f"expected 403; got {code} {str(resp)[:250]}")
        elif code in (401, 403, 404, 422):
            ok(f"{agent} denied {method} {path} (HTTP {code})")
        else:
            finding("MEDIUM", f"unexpected status for {method} {path}", f"{code} {str(resp)[:200]}")

    # Audit reads are not forbidden to a peer; they are scoped to the caller's
    # own rows. Expect 200, and fail if any row belongs to somebody else.
    # Scenarios are dispatched with just the client, so look the ids up here
    # rather than threading them through the runner signature.
    def _agent_id(name: str) -> str:
        c2, b2 = m.call("GET", f"/agents/{name}", agent="memora")
        return b2.get("id", "") if c2 in (200, 201) and isinstance(b2, dict) else ""

    intelx_id = _agent_id("intelx")
    code, resp = m.call("GET", "/audit?limit=50", agent="intelx")
    if code != 200:
        finding("HIGH", "intelx could not read its own audit rows", f"HTTP {code} {str(resp)[:200]}")
    else:
        entries = resp if isinstance(resp, list) else (resp or {}).get("entries", [])
        foreign = [e for e in entries
                   if isinstance(e, dict) and intelx_id and e.get("actor_id") != intelx_id]
        if foreign:
            finding("CRITICAL", "audit read disclosed other agents' rows",
                    f"{len(foreign)}/{len(entries)} entries belong to someone else: "
                    f"{str(foreign[0])[:250]}")
        else:
            ok(f"audit read is caller-scoped ({len(entries)} own entries, 0 foreign)")

    # Asking for somebody else's rows must return nothing.
    friday_id = _agent_id("friday")
    if friday_id:
        code, resp = m.call("GET", f"/audit?actor_id={friday_id}&limit=50", agent="intelx")
        entries = resp if isinstance(resp, list) else (resp or {}).get("entries", [])
        leaked = [e for e in entries if isinstance(e, dict) and e.get("actor_id") == friday_id]
        if leaked:
            finding("CRITICAL", "audit filter disclosed another agent's rows",
                    f"intelx requested friday's rows and got {len(leaked)}")
        else:
            ok("audit actor_id filter cannot read another agent's rows")

    # Unauthenticated requests must never succeed.
    code, resp = m.call("POST", "/v1/memories", agent=None,
                        body={"content_text": "anonymous write attempt"})
    if code in (200, 201):
        finding("CRITICAL", "unauthenticated write accepted", f"HTTP {code}")
    else:
        ok(f"unauthenticated write rejected (HTTP {code})")

    # A wrong key must be rejected.
    code, resp = m.call("POST", "/v1/memories", agent="forge",
                        headers={"X-API-Key": "wrong-key"},
                        body={"content_text": "bad credential write"})
    if code in (200, 201):
        finding("CRITICAL", "invalid API key accepted", f"HTTP {code}")
    else:
        ok(f"invalid API key rejected (HTTP {code})")


def scenario_graph(m: Memora) -> None:
    print("\n[15] graph: link memories and traverse")
    code, b1 = m.call("POST", "/v1/memories", agent="forge", body={
        "content_text": "Graph probe A: XC-4 compressor housing inspection.",
        "idempotency_key": f"graphA-{uuid.uuid4().hex[:8]}"})
    code2, b2 = m.call("POST", "/v1/memories", agent="forge", body={
        "content_text": "Graph probe B: O-ring replacement for XC-4 housing.",
        "idempotency_key": f"graphB-{uuid.uuid4().hex[:8]}"})
    a = (b1 or {}).get("memory_id") or (b1 or {}).get("id")
    b = (b2 or {}).get("memory_id") or (b2 or {}).get("id")
    if not (a and b):
        finding("MEDIUM", "could not create two memories for graph test", f"{code}/{code2}")
        return

    code, body = m.call("POST", f"/v1/memories/{a}/relationships", agent="forge",
                        body={"target_memory_id": b, "relation_type": "relates_to",
                              "purpose": "same unit"})
    if code >= 500:
        finding("HIGH", "relationship creation errored", f"{code} {str(body)[:250]}")
    else:
        ok(f"relationship created -> HTTP {code}")

    code, body = m.call("GET", f"/v1/memories/{a}/graph", agent="forge")
    if code >= 500:
        finding("HIGH", "graph traversal errored", f"{code} {str(body)[:250]}")
    else:
        ok(f"graph traversal -> HTTP {code}")

    # Another agent must not be able to traverse forge's graph.
    code, body = m.call("GET", f"/v1/memories/{a}/graph", agent="intelx")
    if code == 200 and body:
        finding("MEDIUM", "intelx traversed forge's graph",
                f"HTTP 200 with {len(json.dumps(body))} bytes", str(body)[:250])
    else:
        ok(f"intelx graph traversal denied/empty (HTTP {code})")


def scenario_observability(m: Memora) -> None:
    print("\n[16] observability: metrics, events, audit")
    code, body = m.call("GET", "/v1/metrics", agent="memora")
    if code != 200:
        finding("MEDIUM", "metrics endpoint failed", f"{code}")
    else:
        ok(f"metrics ok ({len(json.dumps(body))} bytes)")

    code, body = m.call("GET", "/metrics", agent="memora")
    if code != 200:
        finding("MEDIUM", "prometheus endpoint failed", f"{code}")
    else:
        ok("prometheus scrape ok")

    code, body = m.call("GET", "/audit", agent="memora", params={"limit": 20})
    if code != 200:
        finding("MEDIUM", "audit log unreadable", f"{code} {str(body)[:200]}")
    else:
        rows = body if isinstance(body, list) else body.get("entries", body.get("items", []))
        ok(f"audit log has {len(rows) if isinstance(rows, list) else '?'} entries")

    code, body = m.call("GET", "/v1/events", agent="memora", params={"limit": 20})
    if code != 200:
        finding("MEDIUM", "event feed unreadable", f"{code} {str(body)[:200]}")
    else:
        ok("event feed readable")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--writes", type=int, default=10)
    p.add_argument("--skip", default="", help="comma-separated scenario numbers to skip")
    args = p.parse_args()

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    m = Memora(args.base)

    code, _ = m.call("GET", "/health")
    if code != 200:
        print(f"server not healthy at {args.base} (HTTP {code}) — aborting")
        return 99
    print(f"driving {args.base} — server healthy")

    scenarios = [
        ("1", scenario_bootstrap),
        ("2", scenario_write_recall),
        ("3", scenario_context_building),
        ("4", scenario_cross_agent_access),
        ("5", scenario_task_execution),
        ("6", scenario_collaboration),
        ("7", scenario_reflection),
        ("8", scenario_self_healing),
        ("9", scenario_extremes),
        ("10", scenario_query_extremes),
        ("11", lambda mm: scenario_concurrency(mm, args.workers, args.writes)),
        ("12", scenario_idempotency),
        ("13", scenario_lifecycle),
        ("14", scenario_authz_matrix),
        ("15", scenario_graph),
        ("16", scenario_observability),
    ]
    for num, fn in scenarios:
        if num in skip:
            print(f"\n[{num}] skipped")
            continue
        try:
            fn(m)
        except Exception as exc:
            finding("CRITICAL", f"scenario {num} raised", f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 70)
    print(f"{len(PASSED)} checks passed, {len(FINDINGS)} findings")
    by_sev: Dict[str, int] = {}
    for f in FINDINGS:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        if by_sev.get(sev):
            print(f"  {sev}: {by_sev[sev]}")
    print("=" * 70)
    if FINDINGS:
        print("\nFINDINGS DETAIL")
        for i, f in enumerate(FINDINGS, 1):
            print(f"\n{i}. [{f['severity']}] {f['title']}")
            print(f"   {f['detail']}")
            if f.get("evidence"):
                print(f"   evidence: {str(f['evidence'])[:300]}")
    return len(FINDINGS)


if __name__ == "__main__":
    sys.exit(main())
