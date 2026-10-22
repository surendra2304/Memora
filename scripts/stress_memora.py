"""
Concurrency and load harness for Memora.

The pytest suite runs one request at a time against an in-memory SQLite with
StaticPool, which cannot surface race conditions, lost writes, or cross-tenant
leaks under contention. This harness drives the real ASGI app with many
concurrent workers and asserts on invariants that must hold regardless of
scheduling.

Run:  python scripts/stress_memora.py [--agents 8 --writes 40 --readers 8]
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import tempfile
import time
from collections import Counter

from sqlalchemy import func
from concurrent.futures import ThreadPoolExecutor, as_completed

# A real file-backed SQLite, not StaticPool in-memory, so contention is genuine.
_TMPDIR = tempfile.mkdtemp(prefix="memora_stress_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR}/stress.db"
os.environ["SQLITE_FALLBACK_URL"] = f"sqlite:///{_TMPDIR}/stress.db"
# Development mode so SQLite is an acceptable backend (production correctly
# refuses it as non-durable). Auth is still fully enforced because real per-agent
# API keys are set below and MEMORA_ALLOW_ANONYMOUS_DEV is explicitly unset.
os.environ["MEMORA_ENV"] = "development"
os.environ.pop("MEMORA_ALLOW_ANONYMOUS_DEV", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from apps.api.main import app  # noqa: E402
from storage.relational.session import SessionLocal  # noqa: E402
from storage.relational.models import MemoryRecord  # noqa: E402

WORDS = [
    "xenon", "compressor", "torque", "seal", "calibration", "reactor", "coolant",
    "manifold", "actuator", "telemetry", "handshake", "quota", "ledger", "shard",
]


def _headers(agent: str, key: str) -> dict:
    return {"X-Agent-Name": agent, "X-API-Key": key, "X-Access-Purpose": "collaboration"}


def _payload(agent: str, seq: int) -> dict:
    body = " ".join(random.sample(WORDS, 5))
    return {
        "content_text": f"{agent} observation {seq}: {body}",
        "idempotency_key": f"{agent}-{seq}",
    }


def run(agents: int, writes: int, readers: int, rounds: int) -> int:
    # authenticate_agent only recognises the nine ecosystem agents, so the
    # harness must use their real names to exercise the authenticated path.
    roster = ["friday", "forge", "sentinel", "intelx", "cortex",
              "stratex", "inference", "futuris", "memora"]
    if agents > len(roster):
        raise SystemExit(f"--agents max is {len(roster)} (the known ecosystem roster)")
    names = roster[:agents]
    keys = {name: f"key-{name}-secret" for name in names}
    for name, key in keys.items():
        os.environ[f"{name.upper()}_API_KEY"] = key

    failures: list[str] = []
    status_counts: Counter = Counter()

    client = TestClient(app)
    with client:
        health = client.get("/health")
        if health.status_code != 200:
            print(f"FATAL: app did not boot: {health.status_code} {health.text[:200]}")
            return 1
        print(f"booted OK; {len(app.openapi()['paths'])} routes; hammering with "
              f"{agents} agents x {writes} writes + {readers} concurrent readers\n")

        # ---- phase 1: concurrent writes ------------------------------------
        t0 = time.perf_counter()

        def write_one(agent: str, seq: int):
            try:
                r = client.post("/v1/memories", json=_payload(agent, seq),
                                headers=_headers(agent, keys[agent]))
                return r.status_code, agent, seq, r.text[:160]
            except Exception as exc:  # a raised exception IS a finding
                return "EXC", agent, seq, f"{type(exc).__name__}: {exc}"

        write_results = []
        with ThreadPoolExecutor(max_workers=agents * 2) as pool:
            futs = [pool.submit(write_one, a, s)
                    for a in keys for s in range(writes) for _ in range(rounds)]
            for fut in as_completed(futs):
                write_results.append(fut.result())

        write_secs = time.perf_counter() - t0
        for status, *_ in write_results:
            status_counts[str(status)] += 1

        expected_writes = agents * writes * rounds
        print(f"phase 1 writes: {expected_writes} attempted in {write_secs:.2f}s "
              f"({expected_writes / max(write_secs, 1e-9):.0f} req/s)")
        for status, count in sorted(status_counts.items()):
            print(f"    {status}: {count}")

        accepted = status_counts["201"] + status_counts["200"]
        print(f"    accepted: {accepted}/{expected_writes}")
        rejected = sum(c for s, c in status_counts.items() if s.startswith(("4", "5")))
        exc = status_counts["EXC"]

        if exc:
            failures.append(f"{exc} writes raised an exception instead of returning a response")
            for status, agent, seq, text in write_results:
                if status == "EXC":
                    failures.append(f"    example: {agent}/{seq} -> {text}")
                    break
        if rejected:
            samples = [t for s, a, q, t in write_results if str(s).startswith(("4", "5"))][:3]
            failures.append(f"{rejected} writes were rejected: {samples}")

        # ---- phase 2: idempotency under contention -------------------------
        # Re-send the exact same payloads concurrently. Every one must resolve to
        # the same stored record, never create a duplicate.
        t1 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=agents * 2) as pool:
            futs = [pool.submit(write_one, a, s) for a in keys for s in range(writes)]
            replay = [f.result() for f in as_completed(futs)]
        replay_secs = time.perf_counter() - t1
        replay_codes = Counter(str(s) for s, *_ in replay)
        print(f"\nphase 2 idempotent replay in {replay_secs:.2f}s: {dict(replay_codes)}")

        # ---- phase 3: verify what actually landed --------------------------
        db = SessionLocal()
        try:
            total = db.query(MemoryRecord).count()
            per_agent = Counter(row[0] for row in db.query(MemoryRecord.agent_id).all())
            # Duplicate check: (tenant, agent, idempotency_key) must be unique.
            # func.count() aggregates WITHIN each group. Using
            # db.query(...).count() here instead builds a scalar subquery over
            # the whole table, which SQLAlchemy folds to a constant: with any
            # rows present it renders "HAVING 1 = 1" and reports every group as
            # a duplicate. That false positive was caught on the first run.
            dupes = (
                db.query(MemoryRecord.tenant_id, MemoryRecord.agent_id,
                         MemoryRecord.idempotency_key)
                .filter(MemoryRecord.idempotency_key.isnot(None))
                .group_by(MemoryRecord.tenant_id, MemoryRecord.agent_id,
                          MemoryRecord.idempotency_key)
                .having(func.count(MemoryRecord.id) > 1)
                .all()
            )
        finally:
            db.close()

        expected_unique = agents * writes
        print(f"\nphase 3 stored rows: {total} (expected exactly {expected_unique} unique)")
        print(f"    rows per agent: {dict(sorted(per_agent.items()))}")
        if total != expected_unique:
            failures.append(
                f"stored {total} rows but expected {expected_unique} — "
                f"lost or duplicated writes under concurrency"
            )
        if dupes:
            failures.append(f"duplicate idempotency keys persisted: {dupes[:5]}")

        # ---- phase 4: concurrent reads while writing -----------------------
        stop = {"flag": False}

        def reader(agent: str):
            codes = Counter()
            while not stop["flag"]:
                try:
                    r = client.post("/v1/memories/query",
                                    json={"query_text": random.choice(WORDS), "limit": 5},
                                    headers=_headers(agent, keys[agent]))
                    codes[r.status_code] += 1
                    if r.status_code == 200:
                        # query_memories returns a bare list, not an envelope.
                        body = r.json()
                        items = body if isinstance(body, list) else body.get("results", [])
                        for item in items:
                            if isinstance(item, dict) and item.get("tenant_id") not in (None, "default"):
                                codes["TENANT_LEAK"] += 1
                except Exception as exc:
                    codes[f"EXC:{type(exc).__name__}"] += 1
            return codes

        t2 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=readers + agents) as pool:
            reader_futs = [pool.submit(reader, a) for a in list(keys)[:readers]]
            writer_futs = [pool.submit(write_one, a, s)
                           for a in keys for s in range(writes, writes * 2)]
            for f in as_completed(writer_futs):
                f.result()
            stop["flag"] = True
            read_codes = Counter()
            for f in reader_futs:
                read_codes.update(f.result())
        read_secs = time.perf_counter() - t2

        read_total = sum(v for k, v in read_codes.items() if isinstance(k, int))
        print(f"\nphase 4 concurrent read/write for {read_secs:.2f}s: "
              f"{read_total} reads ({read_total / max(read_secs, 1e-9):.0f} req/s)")
        for code, count in sorted(read_codes.items(), key=lambda kv: str(kv[0])):
            print(f"    {code}: {count}")

        if read_codes.get("TENANT_LEAK"):
            failures.append(f"{read_codes['TENANT_LEAK']} reads returned another tenant's data")
        read_errors = sum(v for k, v in read_codes.items()
                          if isinstance(k, int) and k >= 500)
        if read_errors:
            failures.append(f"{read_errors} reads returned 5xx under load")
        read_exc = sum(v for k, v in read_codes.items() if str(k).startswith("EXC"))
        if read_exc:
            failures.append(f"{read_exc} reads raised exceptions under load")

    print("\n" + "=" * 68)
    if failures:
        print(f"FAILED — {len(failures)} invariant violation(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASSED — all invariants held under concurrent load")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", type=int, default=6)
    parser.add_argument("--writes", type=int, default=25)
    parser.add_argument("--readers", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()
    sys.exit(run(args.agents, args.writes, args.readers, args.rounds))
