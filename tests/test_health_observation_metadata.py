from datetime import datetime
import json

from apps.api.routers import health


class DatabaseProbe:
    def execute(self, _statement):
        return 1


def test_health_reports_server_observation_timestamp_and_evidence_class(monkeypatch):
    monkeypatch.setattr(health, "turso_events_configured", lambda: False)
    monkeypatch.setattr(health, "production_mode", lambda: False)
    monkeypatch.setattr(
        health.vector_adapter,
        "readiness",
        lambda: {"status": "connected", "available": True, "backend": "qdrant"},
    )

    body = json.loads(health.health_check(db=DatabaseProbe()).body)

    assert body["evidence_class"] == "server_live_probe"
    observed_at = datetime.fromisoformat(body["observed_at"])
    assert observed_at.tzinfo is not None
