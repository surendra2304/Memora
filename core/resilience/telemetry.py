"""
Mesh Circuit Breaker & Health Telemetry Reporter for Memora.
Gathers real-time resilience metrics, circuit breaker states, database latency,
and dispatches tamper-evident telemetry reports across the FRIDAY Universe private mesh to Sentinel and FRIDAY.
"""
from __future__ import annotations

import datetime
from datetime import timezone
import hashlib
import hmac
import logging
import os
from typing import Any, Dict, Optional
import requests

from core.resilience.circuit_breaker import circuit_registry
from storage.relational.session import storage_receipt
from storage.vector.embedding import EmbeddingGenerator

logger = logging.getLogger(__name__)


class MeshTelemetryReporter:
    """
    Collects circuit breaker, storage, and embedder telemetry and dispatches
    tamper-evident audit and heartbeat payloads across the mesh.
    """

    def __init__(self):
        self.sentinel_url = os.getenv("SENTINEL_URL", "http://10.0.0.120:8008").rstrip("/")
        self.friday_url = os.getenv("FRIDAY_URL", "http://10.0.0.121:10000").rstrip("/")
        self.agent_name = "memora"

    def get_telemetry_snapshot(self) -> Dict[str, Any]:
        """Collect current system telemetry across all subsystems."""
        now = datetime.datetime.now(timezone.utc)
        circuits = circuit_registry.snapshot()
        storage = storage_receipt()
        embedder_mode = EmbeddingGenerator.get_mode()

        open_circuits = [c["name"] for c in circuits.values() if c.get("state") == "open"]
        health_grade = "HEALTHY"
        if open_circuits or not storage.get("durable", False):
            health_grade = "DEGRADED"

        return {
            "agent": self.agent_name,
            "timestamp": now.isoformat(),
            "health_grade": health_grade,
            "storage_backend": storage.get("backend", "unknown"),
            "storage_durable": storage.get("durable", False),
            "embedder_mode": embedder_mode,
            "circuit_count": len(circuits),
            "open_circuits": open_circuits,
            "circuits": circuits,
        }

    def _sign_payload(self, payload: Dict[str, Any], key: str) -> str:
        """Compute HMAC-SHA256 signature for telemetry packet."""
        data_str = hashlib.sha256(str(sorted(payload.items())).encode("utf-8")).hexdigest()
        return hmac.new(key.encode("utf-8"), data_str.encode("utf-8"), hashlib.sha256).hexdigest()

    def report_to_sentinel(self, telemetry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Dispatch signed audit telemetry to Sentinel (:8008)."""
        data = telemetry or self.get_telemetry_snapshot()
        endpoint = f"{self.sentinel_url}/v1/audit/record"
        signing_key = os.getenv("SENTINEL_AUDIT_HMAC_KEY")
        api_key = os.getenv("SENTINEL_API_KEY")
        if not signing_key or not api_key:
            logger.error("Sentinel telemetry is disabled: required credentials are not configured")
            return {
                "sentinel_status": "misconfigured (missing required credential)",
                "endpoint": endpoint,
            }

        sig = self._sign_payload(data, signing_key)
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Name": "Memora",
            "Authorization": f"Bearer {api_key}",
            "X-Signature": sig,
        }

        packet = {
            "source_agent": "memora",
            "event_type": "mesh.telemetry.circuit_breakers",
            "timestamp": data["timestamp"],
            "payload": data,
            "signature": sig,
        }

        try:
            resp = requests.post(endpoint, json=packet, headers=headers, timeout=2.5)
            return {
                "sentinel_status": "delivered" if resp.status_code in {200, 201, 202} else f"http_{resp.status_code}",
                "endpoint": endpoint,
            }
        except Exception as exc:
            logger.debug("Sentinel telemetry dispatch failed (non-blocking): %s", exc)
            return {"sentinel_status": f"unreachable ({type(exc).__name__})", "endpoint": endpoint}

    def report_to_friday(self, telemetry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Dispatch mesh heartbeat telemetry to FRIDAY (:10000)."""
        data = telemetry or self.get_telemetry_snapshot()
        endpoint = f"{self.friday_url}/api/v1/mesh/heartbeat"
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Name": "Memora",
            "Authorization": f"Bearer {os.getenv('FRIDAY_API_KEY', '')}",
        }

        try:
            resp = requests.post(endpoint, json=data, headers=headers, timeout=2.5)
            return {
                "friday_status": "delivered" if resp.status_code in {200, 201, 202} else f"http_{resp.status_code}",
                "endpoint": endpoint,
            }
        except Exception as exc:
            logger.debug("FRIDAY heartbeat dispatch failed (non-blocking): %s", exc)
            return {"friday_status": f"unreachable ({type(exc).__name__})", "endpoint": endpoint}

    def report_mesh_telemetry(self) -> Dict[str, Any]:
        """Aggregate telemetry and broadcast to Sentinel and FRIDAY."""
        snapshot = self.get_telemetry_snapshot()
        sentinel_res = self.report_to_sentinel(snapshot)
        friday_res = self.report_to_friday(snapshot)

        return {
            "telemetry": snapshot,
            "sentinel": sentinel_res,
            "friday": friday_res,
        }


mesh_telemetry_reporter = MeshTelemetryReporter()
