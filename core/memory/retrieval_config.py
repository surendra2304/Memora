"""
Retrieval configuration loader for Memora.

`config/retrieval_config.json` shipped with the repository but was never read by
anything, so the documented retrieval weights had no effect and
`SearchService.hybrid_search` silently used its own hardcoded defaults. This
module makes that file authoritative while keeping the service functional when
the file is missing or malformed.
"""
import json
import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "retrieval_config.json",
)

#: Used when the file is absent, unreadable, or missing a key. These match the
#: weights the search service used before the file was wired up, so an absent
#: config reproduces the historical behaviour exactly.
_FALLBACK: Dict[str, Any] = {
    "vector_weight": 0.50,
    "keyword_weight": 0.35,
    "graph_weight": 0.15,
    "entity_boost_weight": 0.10,
    "rrf_k": 60,
    "ranking_factors": {},
}

#: Maps the config file's method names onto the search service's weights.
_METHOD_WEIGHT_KEYS = {
    "dense_vector": "vector_weight",
    "bm25": "keyword_weight",
    "graph_walk": "graph_weight",
}

_lock = threading.Lock()
_cached: Optional[Dict[str, Any]] = None


def _parse(raw: Dict[str, Any]) -> Dict[str, Any]:
    resolved = dict(_FALLBACK)

    for method in raw.get("retrieval_methods", []) or []:
        if not isinstance(method, dict):
            continue
        key = _METHOD_WEIGHT_KEYS.get(str(method.get("name", "")))
        if not key:
            continue
        # A disabled method contributes nothing, regardless of its weight.
        if method.get("enabled") is False:
            resolved[key] = 0.0
            continue
        try:
            resolved[key] = float(method["weight"])
        except (KeyError, TypeError, ValueError):
            logger.warning("retrieval_config: method %r has no usable weight", method.get("name"))

    factors = {}
    for factor in raw.get("ranking_factors", []) or []:
        if not isinstance(factor, dict):
            continue
        name = str(factor.get("name", "")).strip()
        if not name:
            continue
        try:
            factors[name] = float(factor["weight"])
        except (KeyError, TypeError, ValueError):
            continue
    if factors:
        resolved["ranking_factors"] = factors

    return resolved


def get_retrieval_config(reload: bool = False) -> Dict[str, Any]:
    """Return the resolved retrieval configuration, cached after first read."""
    global _cached
    if _cached is not None and not reload:
        return _cached

    with _lock:
        if _cached is not None and not reload:
            return _cached
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ValueError("retrieval_config.json must contain a JSON object")
            _cached = _parse(raw)
            logger.info("Loaded retrieval configuration from %s", _CONFIG_PATH)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Falling back to built-in retrieval weights (%s: %s)", type(exc).__name__, exc
            )
            _cached = dict(_FALLBACK)
    return _cached


def ranking_factor(name: str, default: float = 0.0) -> float:
    """Look up a named ranking factor weight (salience, recency, importance)."""
    factors = get_retrieval_config().get("ranking_factors") or {}
    try:
        return float(factors.get(name, default))
    except (TypeError, ValueError):
        return default
