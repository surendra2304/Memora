"""
Regression tests for the CORS misconfiguration.

apps/api/main.py hardcoded allow_origins=["*"] together with
allow_credentials=True. That pairing is invalid under the CORS spec — browsers
refuse a wildcard Access-Control-Allow-Origin on a credentialed request — and
were a browser to accept it, any site could make authenticated cross-origin
calls to this API.

Origins are now driven by MEMORA_CORS_ORIGINS and credentials are only enabled
when the operator lists explicit origins, never with "*".
"""
import subprocess
import sys
import textwrap

import pytest

from core.config import Settings

#: Runs in a subprocess because the FastAPI app and its middleware are built at
#: import time, so the setting has to be present before apps.api.main is loaded.
_PROBE = textwrap.dedent(
    """
    from fastapi.testclient import TestClient
    from core.config import settings
    from apps.api.main import app

    client = TestClient(app)
    for origin in ("https://app.example.com", "https://evil.example"):
        response = client.get("/health", headers={"Origin": origin})
        print(
            origin,
            response.headers.get("access-control-allow-origin"),
            response.headers.get("access-control-allow-credentials"),
            sep="|",
        )
    """
)


def _cors_headers(env_value: str) -> dict:
    """Boot the app with MEMORA_CORS_ORIGINS=env_value and read the CORS headers."""
    import os

    env = dict(os.environ)
    env["MEMORA_CORS_ORIGINS"] = env_value
    env["PYTHONPATH"] = os.getcwd()
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    headers = {}
    for line in result.stdout.strip().splitlines():
        if "|" not in line:
            continue
        origin, acao, acac = line.split("|")
        headers[origin] = (
            None if acao == "None" else acao,
            None if acac == "None" else acac,
        )
    return headers


def test_default_configuration_allows_no_cross_origin_access():
    """Out of the box this is an agent-to-agent API; browsers get nothing."""
    headers = _cors_headers("")
    acao, _acac = headers["https://evil.example"]
    assert acao is None, "an unconfigured deployment must not emit Access-Control-Allow-Origin"


def test_an_unlisted_origin_is_never_echoed():
    headers = _cors_headers("https://app.example.com")

    allowed_acao, allowed_acac = headers["https://app.example.com"]
    assert allowed_acao == "https://app.example.com"
    assert allowed_acac == "true"

    evil_acao, _ = headers["https://evil.example"]
    assert evil_acao is None, "an origin that was not configured must not be reflected"


def test_wildcard_never_carries_credentials():
    """The invalid "*" + credentials pairing must be impossible."""
    headers = _cors_headers("*")
    acao, acac = headers["https://evil.example"]

    assert acao == "*"
    assert acac is None, 'wildcard origin must not set Access-Control-Allow-Credentials'


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", []),
        ("   ", []),
        ("https://a.example.com", ["https://a.example.com"]),
        ("https://a.example.com,https://b.example.com", ["https://a.example.com", "https://b.example.com"]),
        (" https://a.example.com , https://b.example.com ", ["https://a.example.com", "https://b.example.com"]),
        (",,", []),
    ],
)
def test_origin_list_parsing(value, expected):
    assert Settings(MEMORA_CORS_ORIGINS=value).get_cors_origins() == expected
