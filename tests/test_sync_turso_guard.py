from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "sync_turso.py"


def test_destructive_turso_sync_refuses_without_explicit_flag():
    env = os.environ.copy()
    env["TURSO_AUTH_TOKEN"] = "unit-test-token"
    env["TURSO_DATABASE_URL"] = "https://memora.example.invalid"

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode != 0
    assert "REFUSING destructive Turso rebuild" in result.stderr + result.stdout
    assert "--allow-destructive-rebuild" in result.stderr + result.stdout
    assert "unit-test-token" not in result.stderr + result.stdout
