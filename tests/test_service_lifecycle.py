from __future__ import annotations

import pytest

from apps.api.main import app, lifespan
from storage.convergence.tombstone_worker import tombstone_worker


def test_valid_agents_includes_vyralx():
    """Verify that VALID_AGENTS in apps.api.main includes vyralx for data parity."""
    import inspect
    from apps.api.main import sync_from_turso

    source = inspect.getsource(sync_from_turso)
    assert '"vyralx"' in source or "'vyralx'" in source


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_tombstone_worker(monkeypatch):
    """Verify lifespan context manager launches and shuts down tombstone worker."""
    started = []
    stopped = []

    def mock_start(interval_seconds=15.0):
        started.append(interval_seconds)

    def mock_stop():
        stopped.append(True)

    monkeypatch.setattr("apps.api.main.validate_startup_config", lambda: None)
    monkeypatch.setattr("apps.api.main.init_db", lambda: None)
    monkeypatch.setattr("apps.api.main.storage_ready", lambda: True)
    monkeypatch.setattr("apps.api.main.vector_adapter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.vector_adapter.disconnect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.start_cloud_sync", lambda: True)
    monkeypatch.setattr("apps.api.main.event_emitter.stop_cloud_sync", lambda: None)

    monkeypatch.setattr(tombstone_worker, "start_background_worker", mock_start)
    monkeypatch.setattr(tombstone_worker, "stop_background_worker", mock_stop)

    # Invoke lifespan
    async with lifespan(app):
        assert len(started) == 1
        assert started[0] == 15.0
        assert len(stopped) == 0

    assert len(stopped) == 1


@pytest.mark.asyncio
async def test_lifespan_skips_tombstone_worker_when_storage_not_ready(monkeypatch):
    """Verify lifespan does not start tombstone worker if storage is non-authoritative."""
    started = []

    monkeypatch.setattr("apps.api.main.validate_startup_config", lambda: None)
    monkeypatch.setattr("apps.api.main.init_db", lambda: None)
    monkeypatch.setattr("apps.api.main.storage_ready", lambda: False)
    monkeypatch.setattr("apps.api.main.vector_adapter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.vector_adapter.disconnect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.start_cloud_sync", lambda: True)
    monkeypatch.setattr("apps.api.main.event_emitter.stop_cloud_sync", lambda: None)

    monkeypatch.setattr(tombstone_worker, "start_background_worker", lambda **kw: started.append(True))

    async with lifespan(app):
        assert len(started) == 0


@pytest.mark.asyncio
async def test_lifespan_bootstraps_vyralx_when_valid_key_present(monkeypatch):
    """Verify lifespan triggers VyralX bootstrap when VYRALX_API_KEY >= 32 chars."""
    bootstrap_called = []

    def mock_register():
        bootstrap_called.append(True)
        return "agent-vyralx", "memora://vyralx/private"

    monkeypatch.setenv("VYRALX_API_KEY", "x" * 36)
    monkeypatch.setattr("apps.api.main.validate_startup_config", lambda: None)
    monkeypatch.setattr("apps.api.main.init_db", lambda: None)
    monkeypatch.setattr("apps.api.main.storage_ready", lambda: True)
    monkeypatch.setattr("apps.api.main.vector_adapter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.vector_adapter.disconnect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.start_cloud_sync", lambda: True)
    monkeypatch.setattr("apps.api.main.event_emitter.stop_cloud_sync", lambda: None)

    import scripts.register_vyralx_identity as reg_module
    monkeypatch.setattr(reg_module, "register_vyralx_identity", mock_register)

    async with lifespan(app):
        assert len(bootstrap_called) == 1


@pytest.mark.asyncio
async def test_lifespan_skips_vyralx_bootstrap_when_key_missing_or_short(monkeypatch):
    """Verify lifespan does not trigger VyralX bootstrap when VYRALX_API_KEY is missing or short."""
    bootstrap_called = []

    monkeypatch.setenv("VYRALX_API_KEY", "too_short")
    monkeypatch.setattr("apps.api.main.validate_startup_config", lambda: None)
    monkeypatch.setattr("apps.api.main.init_db", lambda: None)
    monkeypatch.setattr("apps.api.main.storage_ready", lambda: True)
    monkeypatch.setattr("apps.api.main.vector_adapter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.vector_adapter.disconnect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.start_cloud_sync", lambda: True)
    monkeypatch.setattr("apps.api.main.event_emitter.stop_cloud_sync", lambda: None)

    import scripts.register_vyralx_identity as reg_module
    monkeypatch.setattr(reg_module, "register_vyralx_identity", lambda: bootstrap_called.append(True))

    async with lifespan(app):
        assert len(bootstrap_called) == 0


@pytest.mark.asyncio
async def test_lifespan_gracefully_handles_tombstone_worker_shutdown_exception(monkeypatch):
    """Verify lifespan shutdown continues cleanly even if tombstone worker shutdown errors."""
    def broken_stop():
        raise RuntimeError("Worker stop simulated crash")

    monkeypatch.setattr("apps.api.main.validate_startup_config", lambda: None)
    monkeypatch.setattr("apps.api.main.init_db", lambda: None)
    monkeypatch.setattr("apps.api.main.storage_ready", lambda: True)
    monkeypatch.setattr("apps.api.main.vector_adapter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.vector_adapter.disconnect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.connect", lambda: None)
    monkeypatch.setattr("apps.api.main.event_emitter.start_cloud_sync", lambda: True)
    monkeypatch.setattr("apps.api.main.event_emitter.stop_cloud_sync", lambda: None)

    monkeypatch.setattr(tombstone_worker, "start_background_worker", lambda **kw: None)
    monkeypatch.setattr(tombstone_worker, "stop_background_worker", broken_stop)

    # Should not raise exception
    async with lifespan(app):
        pass
