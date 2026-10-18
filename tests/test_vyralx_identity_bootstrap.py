from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import register_vyralx_identity as bootstrap


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _fake_identity(agent, namespace):
    class FakeIdentityService:
        @staticmethod
        def register_agent(db, **kwargs):
            assert kwargs["name"] == "vyralx"
            assert kwargs["tenant_id"] == "default"
            return agent

        @staticmethod
        def resolve_namespace(db, **kwargs):
            assert kwargs["path"] == "memora://vyralx/private"
            assert kwargs["owner_agent_id"] == agent.id
            assert kwargs["tenant_id"] == "default"
            return namespace

    return FakeIdentityService


def test_vyralx_bootstrap_is_scoped_and_preserves_existing_records(monkeypatch, capsys):
    session = _FakeSession()
    agent = SimpleNamespace(id="agent-vyralx", tenant_id="default")
    namespace = SimpleNamespace(
        path="memora://vyralx/private",
        agent_id=agent.id,
        type=bootstrap.NamespaceType.AGENT_PRIVATE,
    )
    monkeypatch.setenv("VYRALX_API_KEY", "v" * 43)
    monkeypatch.setattr(bootstrap, "init_db", lambda: None)
    monkeypatch.setattr(bootstrap, "SessionLocal", lambda: session)
    monkeypatch.setattr(bootstrap, "IdentityService", _fake_identity(agent, namespace))

    agent_id, namespace_path = bootstrap.register_vyralx_identity()

    assert (agent_id, namespace_path) == ("agent-vyralx", "memora://vyralx/private")
    assert session.closed is True
    assert "No existing memory records were deleted or rewritten" in capsys.readouterr().out


def test_vyralx_bootstrap_rejects_a_namespace_owned_by_another_agent(monkeypatch):
    session = _FakeSession()
    agent = SimpleNamespace(id="agent-vyralx", tenant_id="default")
    namespace = SimpleNamespace(
        path="memora://vyralx/private",
        agent_id="agent-sentinel",
        type=bootstrap.NamespaceType.AGENT_PRIVATE,
    )
    monkeypatch.setenv("VYRALX_API_KEY", "v" * 43)
    monkeypatch.setattr(bootstrap, "init_db", lambda: None)
    monkeypatch.setattr(bootstrap, "SessionLocal", lambda: session)
    monkeypatch.setattr(bootstrap, "IdentityService", _fake_identity(agent, namespace))

    with pytest.raises(RuntimeError, match="not owned by the VyralX identity"):
        bootstrap.register_vyralx_identity()
    assert session.closed is True


def test_vyralx_bootstrap_fails_before_touching_database_without_strong_key(monkeypatch):
    monkeypatch.setenv("VYRALX_API_KEY", "short")
    database_touched = False

    def should_not_run():
        nonlocal database_touched
        database_touched = True

    monkeypatch.setattr(bootstrap, "init_db", should_not_run)
    with pytest.raises(RuntimeError, match="VYRALX_API_KEY"):
        bootstrap.register_vyralx_identity()
    assert database_touched is False
