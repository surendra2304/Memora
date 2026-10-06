"""Agent authentication must fail closed, never open.

The guard used to wave through any request that carried no credential whenever the
service was not flagged production. A live local-fleet run reproduced it with plain
curl and no headers at all: POST /v1/memories returned 201 and wrote a memory into the
store every other agent reads, while the same request carrying a wrong key correctly
returned 401. Losing or renaming the ENVIRONMENT value would have silently opened
Memora to unauthenticated writes.
"""

import importlib

import pytest
from fastapi import HTTPException


@pytest.fixture()
def auth(monkeypatch):
    """Loads authenticate_agent with anonymous access explicitly disabled."""
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.delenv("MEMORA_ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    module = importlib.import_module("apps.api.dependencies")
    return importlib.reload(module)


def _headers(**kwargs):
    base = {"x_agent_name": None, "x_api_key": None, "authorization": None}
    base.update(kwargs)
    return base


def test_no_credential_at_all_is_rejected(auth, monkeypatch):
    monkeypatch.setenv("FRIDAY_API_KEY", "f" * 43)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate_agent(**_headers())
    assert exc.value.status_code == 401
    assert "Missing agent credentials" in str(exc.value.detail)


def test_missing_agent_name_is_rejected_even_with_a_key(auth, monkeypatch):
    monkeypatch.setenv("FRIDAY_API_KEY", "f" * 43)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate_agent(**_headers(x_api_key="f" * 43))
    assert exc.value.status_code == 401


def test_development_environment_does_not_open_the_door(auth, monkeypatch):
    """The defect: a non-production service silently accepted anonymous writes."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("FRIDAY_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate_agent(**_headers())
    assert exc.value.status_code == 401


def test_known_agent_with_no_configured_key_reports_a_configuration_fault(auth, monkeypatch):
    """A missing key is a server misconfiguration, not a bad credential."""
    monkeypatch.delenv("FRIDAY_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate_agent(**_headers(x_agent_name="friday", x_api_key="anything"))
    assert exc.value.status_code == 503


def test_wrong_key_is_still_rejected(auth, monkeypatch):
    monkeypatch.setenv("FRIDAY_API_KEY", "f" * 43)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate_agent(**_headers(x_agent_name="friday", x_api_key="z" * 43))
    assert exc.value.status_code == 401
    assert "Invalid agent credentials" in str(exc.value.detail)


def test_matching_key_authenticates(auth, monkeypatch):
    monkeypatch.setenv("FRIDAY_API_KEY", "f" * 43)
    assert auth.authenticate_agent(**_headers(x_agent_name="friday", x_api_key="f" * 43)) == "friday"


def test_anonymous_access_requires_an_explicit_opt_in(auth, monkeypatch):
    monkeypatch.delenv("FRIDAY_API_KEY", raising=False)
    with pytest.raises(HTTPException):
        auth.authenticate_agent(**_headers())
    monkeypatch.setenv("MEMORA_ALLOW_ANONYMOUS_DEV", "1")
    assert auth.authenticate_agent(**_headers(x_agent_name="friday")) == "friday"


def test_an_unregistered_agent_cannot_authenticate(auth, monkeypatch):
    monkeypatch.setenv("ATTACKER_API_KEY", "a" * 43)
    with pytest.raises(HTTPException) as exc:
        auth.authenticate_agent(**_headers(x_agent_name="attacker", x_api_key="a" * 43))
    assert exc.value.status_code == 503
