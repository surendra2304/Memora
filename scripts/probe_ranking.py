#!/usr/bin/env python3
"""
Probe relevance ranking against the live API.

Found earlier: for a specific XC-4 torque query, two generic "Concurrent
observation" rows ranked 1st and 2nd (0.4189 / 0.3903) while the semantically
correct record ranked 3rd (0.1475). This reproduces it in isolation so the cause
can be attributed rather than guessed at.

Usage: python scripts/probe_ranking.py [--base URL]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000"
AGENT = "forge"
KEY = os.environ.get("FORGE_API_KEY", "")


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Agent-Name", AGENT)
    req.add_header("X-API-Key", KEY)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:200]


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base

    run = uuid.uuid4().hex[:6]
    # The write pipeline deduplicates near-identical content, so repeating this
    # probe against the same database collapsed each run's correct answer onto
    # the previous run's record and it never got stored. Vary the substance per
    # run, not just the tag, so every record is genuinely distinct.
    torque = 30 + int(run[:4], 16) % 40
    sample = 100 + int(run[:4], 16) % 800

    # One record that actually answers the question...
    correct = (f"The XC-4 compressor seal torque specification is {torque} Nm "
               f"with a locking compound applied to the retaining bolts [{run}].")
    # ...and several that merely share common vocabulary.
    distractors = [
        f"Concurrent observation {sample}: the system was operating normally "
        f"during the observation window [{run}].",
        f"Concurrent observation {sample + 1}: the reading was within tolerance "
        f"and no action was required [{run}].",
        f"The compressor on unit {sample} was serviced and returned to service [{run}].",
    ]

    for text in [correct] + distractors:
        code, _ = call("POST", "/v1/memories",
                       {"content_text": text, "importance": 0.8})
        if code != 201:
            print(f"could not write: {code}")
            return 1

    queries = [
        "What is the XC-4 compressor seal torque specification?",
        "XC-4 compressor seal torque spec Nm",
        "torque specification",
    ]

    problems = 0
    for q in queries:
        # /search is used rather than /query because /query responds with
        # MemoryRecordRead, which carries no score fields at all - a caller
        # cannot see why results are ordered as they are.
        code, resp = call("GET", f"/v1/memories/search?q={urllib.parse.quote(q)}&limit=10")
        rows = resp if isinstance(resp, list) else (resp or {}).get("results", [])
        # Repeated runs accumulate near-duplicate records and the write pipeline
        # deduplicates them, which makes the comparison meaningless. Keep only
        # this run's own records so the ranking is measured in isolation.
        rows = [r for r in rows if run in str(r.get("content_text", ""))]
        print(f"\nquery: {q!r}   (HTTP {code}, {len(rows)} rows from this run)")
        rank_of_correct = None
        for i, r in enumerate(rows, 1):
            text = str(r.get("content_text", ""))
            score = r.get("final_score")
            parts = (f"sem={r.get('semantic_score')} kw={r.get('keyword_score')} "
                     f"g={r.get('graph_boost')}")
            mark = ""
            if run in text:
                if "compressor seal torque specification" in text:
                    mark = "   <== CORRECT ANSWER"
                    rank_of_correct = i
                else:
                    mark = "   (distractor)"
            print(f"  {i}. final={score!s:>7} [{parts}]  {text[:60]}{mark}")
        if rank_of_correct is None:
            print("  NOTE the correct answer is not in the top 10 at all")
            problems += 1
        elif rank_of_correct > 1 and len(rows) > 1:
            print(f"  NOTE the correct answer ranks {rank_of_correct}, "
                  f"not first")
            problems += 1
        else:
            print("  ok   the correct answer ranks first")

    print("\n" + "=" * 70)
    print(f"ranking problems: {problems}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
