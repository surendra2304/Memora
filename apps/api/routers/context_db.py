"""Memora context-database routes (``viking://``).

Thin API shim over ``core.memory.context_service`` so the layers, scoped search
and session commits are reachable from Memora's REST surface exactly like the
relational memory routes.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SERVICE_PATH = Path(__file__).resolve().parents[3] / "core" / "memory" / "context_service.py"

if "memora_context_service" in sys.modules:
    _service = sys.modules["memora_context_service"]
else:
    _spec = importlib.util.spec_from_file_location("memora_context_service", _SERVICE_PATH)
    _service = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    sys.modules["memora_context_service"] = _service
    _spec.loader.exec_module(_service)  # type: ignore[union-attr]

router = _service.router
get_service = _service.get_service
MemoraContextService = _service.MemoraContextService
FabricBridge = _service.FabricBridge

__all__ = ["router", "get_service", "MemoraContextService", "FabricBridge"]
