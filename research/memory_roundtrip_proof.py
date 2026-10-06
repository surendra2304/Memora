"""Prove the Memora mesh credential path actually works, using real keys.

The live probe found every agent rejecting placeholder credentials. Before
concluding "the code is fine, only the deployment secrets are missing", this
runs the SAME endpoints against a REAL Memora process started locally with a
REAL 32+ character key and the REAL Turso database, so the auth, namespace,
write and recall paths are all exercised for real.

If this passes, the failure in the cloud is proven to be configuration, not code.

Run it:

    python research/memory_roundtrip_proof.py
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]

# A real, unique, high-entropy key — exactly the shape the production guard demands.
REAL_KEY = "friday_" + secrets.token_urlsafe(32)
CORR = f"roundtrip-{uuid.uuid4().hex[:10]}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_dotenv() -> None:
    """Load Memora's real .env so Turso is genuinely used, not faked."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        print("FATAL: Memora/.env is missing; cannot prove against real Turso.")
        sys.exit(2)
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    load_dotenv()
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "MEMORA_ENV": "production",  # exercise the production guard, not the dev bypass
            "ENVIRONMENT": "production",
            "FRIDAY_API_KEY": REAL_KEY,
            "PORT": str(port),
        }
    )

    print(f"=== MEMORA ROUND-TRIP PROOF  corr={CORR}  port={port} ===")
    print(f"key: {REAL_KEY[:14]}... ({len(REAL_KEY)} chars, real random value)\n")

    log_path = REPO_ROOT / "reports_and_data" / f"memora-proof-server-{CORR}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base = f"http://127.0.0.1:{port}"
    try:
        # Wait for real startup, bounded.
        deadline = time.time() + 120
        up = False
        while time.time() < deadline:
            if proc.poll() is not None:
                log_file.flush()
                print("FATAL: Memora process exited during startup:")
                print(log_path.read_text(encoding="utf-8", errors="replace")[-3000:])
                return 2
            try:
                # Any HTTP answer means the process is serving. Memora answers
                # 503 from /health when Turso is unreachable, which is honest
                # reporting, not a failed startup — treating that as "not up"
                # would hide the real question this proof asks about auth.
                if httpx.get(f"{base}/health", timeout=3).status_code in (200, 503):
                    up = True
                    break
            except Exception:
                pass
            time.sleep(2)
        if not up:
            log_file.flush()
            print("FATAL: Memora did not become healthy within 120s.")
            print(f"server log: {log_path}")
            print(log_path.read_text(encoding="utf-8", errors="replace")[-3000:])
            return 2
        print("Memora process is live and healthy.\n")

        results: list[dict] = []

        def check(name: str, ok: bool, detail: str) -> None:
            results.append({"check": name, "ok": ok, "detail": detail})
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

        # ------------------------------------------------------------------
        # 1. Wrong / missing credentials must be REFUSED (security is real).
        r = httpx.get(f"{base}/v1/memories/search?q=test", timeout=20)
        check(
            "unauthenticated request is refused",
            r.status_code == 401,
            f"HTTP {r.status_code} (expected 401) body={r.text[:120]}",
        )

        r = httpx.get(
            f"{base}/v1/memories/search?q=test",
            headers={"X-Agent-Name": "friday", "X-API-Key": "friday_wrong_key_value_000000"},
            timeout=20,
        )
        check(
            "wrong key is refused",
            r.status_code == 401,
            f"HTTP {r.status_code} (expected 401)",
        )

        # ------------------------------------------------------------------
        # 2. Real credential must be ACCEPTED.
        good = {"X-Agent-Name": "friday", "X-API-Key": REAL_KEY}
        marker = f"ROUNDTRIP_PROOF_{CORR}"
        r = httpx.post(
            f"{base}/v1/memories",
            headers=good,
            json={
                "agent_name": "friday",
                "namespace": "universal",
                "content": marker + ": a real durable memory written by the round-trip proof",
                "memory_type": "episodic",
                "metadata": {"proof": CORR, "correlation_id": CORR},
            },
            timeout=45,
        )
        wrote = r.status_code in (200, 201)
        check(
            "authenticated write accepted",
            wrote,
            f"HTTP {r.status_code} body={r.text[:220]}",
        )

        # ------------------------------------------------------------------
        # 3. The memory must be genuinely recallable, not just accepted.
        r = httpx.get(f"{base}/v1/memories/search", params={"q": marker}, headers=good, timeout=30)
        found = False
        detail = f"HTTP {r.status_code} body={r.text[:200]}"
        if r.status_code == 200:
            blob = r.text
            found = marker in blob
            detail = f"HTTP 200, marker_present={found}"
        check("written memory is recallable by content", found, detail)

        # ------------------------------------------------------------------
        # 4. Scope isolation must hold (a private record is not universal).
        r = httpx.post(
            f"{base}/v1/memories",
            headers=good,
            json={
                "agent_name": "friday",
                "namespace": "agent",
                "scope_id": "friday",
                "content": marker + "_PRIVATE: private record for isolation check",
                "memory_type": "episodic",
                "metadata": {"proof": CORR},
            },
            timeout=45,
        )
        check(
            "scoped private write accepted",
            r.status_code in (200, 201),
            f"HTTP {r.status_code} body={r.text[:200]}",
        )

        r = httpx.get(f"{base}/v1/memories/search", params={"q": marker + "_PRIVATE"}, headers=good, timeout=30)
        check(
            "private memory is visible to its owner",
            r.status_code == 200 and marker + "_PRIVATE" in r.text,
            f"HTTP {r.status_code}",
        )

        # ------------------------------------------------------------------
        # 5. A different agent must not be able to use friday's key + identity.
        r = httpx.get(
            f"{base}/v1/memories/search",
            params={"q": marker},
            headers={"X-Agent-Name": "cortex", "X-API-Key": REAL_KEY},
            timeout=20,
        )
        check(
            "friday's key is rejected when presented as another agent",
            r.status_code == 401,
            f"HTTP {r.status_code} (expected 401) — identity is bound to the key",
        )

        passed = sum(1 for x in results if x["ok"])
        print(f"\n{'=' * 70}")
        print(f"  {passed}/{len(results)} checks passed  (real Turso, real key, real HTTP)")
        print("=" * 70)
        verdict = (
            "The memory mesh path is CORRECT. The cloud failure is missing Render secrets, not broken code."
            if passed == len(results)
            else "The memory path has a real defect."
        )
        print(verdict)

        out = REPO_ROOT / "reports_and_data" / f"memora-roundtrip-proof-{CORR}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"correlation_id": CORR, "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "results": results, "verdict": verdict},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"artifact: {out.name}")
        return 0 if passed == len(results) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.kill()
        try:
            log_file.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())