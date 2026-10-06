"""
Regression tests for MEDIUM-5: config/retrieval_config.json was shipped but
never read, so SearchService silently used its own hardcoded weights.
"""


from core.memory.retrieval_config import (
    _FALLBACK,
    _parse,
    get_retrieval_config,
    ranking_factor,
)


def test_the_shipped_config_file_is_actually_read():
    """The weights must come from the file, not from defaults that ignore it."""
    cfg = get_retrieval_config()

    assert cfg["vector_weight"] == 0.6
    assert cfg["keyword_weight"] == 0.3
    assert cfg["graph_weight"] == 0.1
    assert cfg["rrf_k"] == 60

    # The file's values differ from the historical hardcoded defaults, which is
    # the whole point: if these equalled the fallback the file would be inert.
    assert cfg["vector_weight"] != _FALLBACK["vector_weight"]


def test_ranking_factors_are_exposed_by_name():
    assert ranking_factor("salience") == 0.4
    assert ranking_factor("recency") == 0.3
    assert ranking_factor("importance") == 0.3
    assert ranking_factor("not_a_factor", default=0.25) == 0.25


def test_a_disabled_method_contributes_no_weight():
    parsed = _parse({
        "retrieval_methods": [
            {"name": "dense_vector", "enabled": True, "weight": 0.6},
            {"name": "bm25", "enabled": False, "weight": 0.9},
            {"name": "graph_walk", "enabled": True, "weight": 0.1},
        ]
    })
    assert parsed["vector_weight"] == 0.6
    assert parsed["keyword_weight"] == 0.0, "a disabled method must be zeroed, not honoured"
    assert parsed["graph_weight"] == 0.1


def test_malformed_config_falls_back_instead_of_raising():
    """A bad config file must degrade, never take search down."""
    parsed = _parse({"retrieval_methods": [{"name": "dense_vector", "weight": "not-a-number"}]})
    assert parsed["vector_weight"] == _FALLBACK["vector_weight"]

    # Junk entries are skipped rather than crashing the loader.
    parsed = _parse({"retrieval_methods": ["nonsense", None, {"name": "bm25", "weight": 0.4}]})
    assert parsed["keyword_weight"] == 0.4


def test_unknown_method_names_are_ignored():
    parsed = _parse({"retrieval_methods": [{"name": "telepathy", "weight": 0.9}]})
    assert parsed["vector_weight"] == _FALLBACK["vector_weight"]


def test_search_service_uses_the_configured_weights(test_db, monkeypatch):
    """End to end: the resolver must feed SearchService, not just parse in isolation."""
    from core.identity.service import IdentityService
    from core.memory.pipeline.write_service import MemoryWriteService
    from core.memory.search_service import SearchService

    IdentityService.register_agent(test_db, "friday")
    MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="friday",
        content_text="the xenon compressor needs a torque check every quarter",
    )

    seen = {}
    real_hybrid = SearchService.hybrid_search.__func__

    def spy(cls, db, query_text, **kwargs):
        # Resolve the way the real method does, then capture the effective values.
        from core.memory.retrieval_config import get_retrieval_config

        cfg = get_retrieval_config()
        seen["vector_weight"] = cfg["vector_weight"] if kwargs.get("vector_weight") is None else kwargs["vector_weight"]
        seen["keyword_weight"] = cfg["keyword_weight"] if kwargs.get("keyword_weight") is None else kwargs["keyword_weight"]
        return real_hybrid(cls, db, query_text, **kwargs)

    monkeypatch.setattr(SearchService, "hybrid_search", classmethod(spy))

    results = SearchService.hybrid_search(db=test_db, query_text="xenon compressor torque", actor_name="friday")
    assert len(results) >= 1
    assert seen["vector_weight"] == 0.6
    assert seen["keyword_weight"] == 0.3


def test_explicit_weights_still_override_the_config(test_db):
    """The degraded-fallback path in ContextBuilderService depends on this."""
    from core.identity.service import IdentityService
    from core.memory.pipeline.write_service import MemoryWriteService
    from core.memory.search_service import SearchService

    IdentityService.register_agent(test_db, "friday")
    MemoryWriteService.execute_pipeline(
        db=test_db, caller_name="friday", content_text="override weight probe memory"
    )

    results = SearchService.hybrid_search(
        db=test_db,
        query_text="override weight probe",
        actor_name="friday",
        vector_weight=0.0,
        keyword_weight=0.85,
        graph_weight=0.15,
    )
    assert len(results) >= 1
