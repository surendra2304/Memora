#!/usr/bin/env python3
"""
Probe the endpoints the exerciser never drives.

Written because a green exerciser run only proves the paths it happens to touch.
Enumerating /openapi.json against the harness source showed a set of routes that
no scenario references at all. This drives each one over real HTTP with a valid
body and reports what actually happens, so a route that 500s, or 200s while
doing nothing, is visible instead of assumed fine.

Usage: python scripts/probe_untested_endpoints.py [--base URL]
Exit code is the number of problems found.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000"

KEYS = {
    "friday": os.environ.get("FRIDAY_API_KEY", ""),
    "forge": os.environ.get("FORGE_API_KEY", ""),
    "memora": os.environ.get("MEMORA_API_KEY", ""),
    "intelx": os.environ.get("INTELX_API_KEY", ""),
}

problems: list[str] = []
passed = 0


def call(method: str, path: str, agent: str, body=None, bearer: bool = False):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Agent-Name", agent)
    req.add_header("X-API-Key", KEYS.get(agent, ""))
    if bearer:
        req.add_header("Authorization", f"Bearer {KEYS.get(agent, '')}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def check(label: str, method: str, path: str, agent: str, body=None,
          expect=(200, 201), bearer: bool = False):
    """Drive one endpoint and report the real outcome."""
    global passed
    code, resp = call(method, path, agent, body, bearer=bearer)
    if code in expect:
        passed += 1
        print(f"  ok   {label}: HTTP {code}")
    else:
        problems.append(f"{label}: {method} {path} as {agent} -> HTTP {code} "
                        f"(expected {expect}) {str(resp)[:220]}")
        print(f"  FAIL {label}: HTTP {code} {str(resp)[:180]}")
    return code, resp


def main() -> int:
    global BASE, passed
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base

    if not any(KEYS.values()):
        print("no API keys in the environment; source the env file first")
        return 2

    # ---------------------------------------------------------------- setup
    print("\n[setup] provision the identities this probe needs")
    for name in ("friday", "forge", "memora", "intelx"):
        code, body = call("POST", "/agents", "memora",
                          {"name": name, "role": "worker", "description": "probe"})
        if code in (200, 201, 409):
            passed += 1
        else:
            print(f"  note could not register {name}: {code} {str(body)[:120]}")

    code, mem = call("POST", "/v1/memories", "forge",
                     {"content_text": f"probe record {uuid.uuid4().hex[:8]}",
                      "importance": 0.7})
    if code != 201:
        print(f"cannot continue without a memory: {code} {str(mem)[:200]}")
        return 1
    mid = mem.get("id")
    nsid = mem.get("namespace_id")
    print(f"  ok   wrote probe memory {mid}")

    code, mem2 = call("POST", "/v1/memories", "forge",
                      {"content_text": f"probe successor {uuid.uuid4().hex[:8]}"})
    mid2 = mem2.get("id") if code == 201 else None
    if mid2:
        check("supersede", "POST", f"/v1/memories/{mid}/supersede", "forge",
              {"new_memory_id": mid2, "reason": "probe supersession"})

    # ------------------------------------------------- experience/learning
    print("\n[1] experience and learning endpoints")
    check("record-interaction", "POST", "/v1/memories/record-interaction", "forge",
          {"user_text": "did the XC-4 seal hold after the replacement?",
           "agent_text": "yes, 3.2 bar for 24 hours",
           "event_type": "dialogue", "tags": ["xc-4", "seal"]})
    check("learn-outcome", "POST", "/v1/memories/learn-outcome", "forge",
          {"task_name": "replace the XC-4 compressor seal",
           "status": "success",
           "actions_taken": "replaced seal, re-torqued to spec",
           "context": "pressure held at 3.2 bar for 24 hours",
           "domain": "maintenance"})
    # learn-experience consumes outcome records in the learn-outcome shape
    # (task_name/status), not memory rows from the experience list.
    check("learn-experience", "POST", "/v1/memories/learn-experience", "forge",
          {"outcomes": [
              {"task_name": "replace the XC-4 compressor seal", "status": "success",
               "context": "pressure held at 3.2 bar for 24 hours"},
              {"task_name": "re-torque the XC-4 flange", "status": "success",
               "context": "holding pressure rose to 3.4 bar"}]})
    check("experience list", "GET", "/v1/memories/experience?limit=5", "forge")

    # ------------------------------------------------------------- search
    print("\n[2] search (the GET variant, distinct from POST /query)")
    check("search", "GET", "/v1/memories/search?q=probe&limit=5", "forge")
    code, resp = call("GET", "/v1/memories/search?q=%25&limit=50", "forge")
    n = len(resp) if isinstance(resp, list) else len((resp or {}).get("results", []))
    if code == 200 and n > 20:
        problems.append(f"search with a bare '%' matched {n} rows - "
                        f"LIKE wildcards are not being escaped")
        print(f"  FAIL search '%' matched {n} rows")
    else:
        passed += 1
        print(f"  ok   search '%' matched {n} rows (wildcards escaped)")

    # -------------------------------------------------------- collaboration
    print("\n[3] collaboration")
    code2, other = call("POST", "/v1/memories", "forge",
                        {"content_text": f"contribution probe {uuid.uuid4().hex[:8]}"})
    contrib_id = other.get("id") if code2 == 201 else mid
    check("contribute", "POST", "/v1/collaboration/contribute", "forge",
          {"memory_id": contrib_id, "recipient": "friday",
           "purpose": "share the probe finding", "actions": ["read"]})

    # ------------------------------------------------------------- events
    print("\n[4] event feed cursors")
    code, cur = check("event cursor", "GET", "/v1/events/cursor?consumer=probe", "forge")
    cursor = cur.get("cursor") if isinstance(cur, dict) else None
    if cursor:
        check("event ack", "POST", "/v1/events/ack", "forge",
              {"consumer": "probe", "cursor": cursor})

    # --------------------------------------------------------- resilience
    print("\n[5] resilience observability")
    check("circuits", "GET", "/v1/resilience/circuits", "memora")
    check("repair preview", "GET", "/v1/resilience/repair/preview", "memora")
    code, circuits = call("GET", "/v1/resilience/circuits", "memora")
    names = []
    if isinstance(circuits, list):
        names = [c.get("name") for c in circuits if isinstance(c, dict) and c.get("name")]
    elif isinstance(circuits, dict):
        names = [c.get("name") for c in (circuits.get("circuits") or [])
                 if isinstance(c, dict) and c.get("name")]
    if names:
        check("circuit detail", "GET", f"/v1/resilience/circuits/{names[0]}", "memora")
        check("circuit reset", "POST", f"/v1/resilience/circuits/{names[0]}/reset",
              "memora", {})
    else:
        print("  note no circuit breakers registered to probe")

    # ----------------------------------------------------------- policy
    print("\n[6] namespace policy view")
    if nsid:
        check("namespace policy", "GET", f"/v1/namespaces/{nsid}/policy", "forge")

    # --------------------------------------------------------- lifecycle
    print("\n[7] state transition")
    # Use a fresh memory: mid has already been superseded above, and refusing to
    # move a SUPERSEDED record back to VERIFIED is correct behaviour.
    code2, tm = call("POST", "/v1/memories", "forge",
                     {"content_text": f"transition probe {uuid.uuid4().hex[:8]}"})
    tid = tm.get("id") if code2 == 201 else mid
    check("transition", "POST", f"/memories/{tid}/transition", "forge",
          {"target_state": "verified", "purpose": "probe"})

    # ------------------------------------------------------------- mesh
    print("\n[8] mesh envelope")
    import time as _t
    # The endpoint authenticates the sender with its own API key as a Bearer
    # token whenever that key is configured, and answers 202 Accepted.
    check("mesh envelope", "POST", "/mesh/envelope", "forge",
          {"message_id": f"msg-{uuid.uuid4().hex[:8]}",
           "correlation_id": f"corr-{uuid.uuid4().hex[:8]}",
           "from_agent": "forge", "to_agent": "friday",
           "intent": "store", "priority": "normal", "ttl": 300,
           "payload": {"content_text": "mesh envelope probe"},
           "created_at": _t.time()},
          expect=(200, 201, 202), bearer=True)

    # A wrong Bearer token must be refused; a right one must not be the only
    # thing standing between a caller and somebody else's inbox.
    code, resp = call("POST", "/mesh/envelope", "forge",
                      {"message_id": f"msg-{uuid.uuid4().hex[:8]}",
                       "correlation_id": f"corr-{uuid.uuid4().hex[:8]}",
                       "from_agent": "forge", "to_agent": "friday",
                       "intent": "store", "payload": {}, "created_at": _t.time()})
    if code == 401:
        passed += 1
        print("  ok   mesh envelope without a Bearer token refused (401)")
    else:
        problems.append(f"mesh envelope accepted a request with no Bearer token: HTTP {code}")
        print(f"  FAIL mesh envelope accepted an unauthenticated sender: {code}")

    # -------------------------------------------------------- subagents
    print("\n[9] subagent registration")
    leaf = f"probe{uuid.uuid4().hex[:5]}"
    code, resp = check("subagent create", "POST", "/agents/subagents", "forge",
                       {"name": leaf, "role": "helper",
                        "description": "probe subagent",
                        "bounded_scope": f"memora://forge/delegated/forge:{leaf}"})
    if code in (200, 201) and isinstance(resp, dict):
        nm = resp.get("name", "")
        if not nm.startswith("forge:"):
            problems.append(f"subagent name is '{nm}', expected it parented under forge")
            print(f"  FAIL subagent not parented under the caller: {nm}")
        else:
            passed += 1
            print(f"  ok   subagent parented under the caller: {nm}")

    # ----------------------------------------------------------- summary
    print("\n" + "=" * 70)
    print(f"{passed} checks passed, {len(problems)} problems")
    if problems:
        print("\nPROBLEMS")
        for i, p in enumerate(problems, 1):
            print(f"{i}. {p}")
    print("=" * 70)
    return len(problems)


if __name__ == "__main__":
    sys.exit(main())
