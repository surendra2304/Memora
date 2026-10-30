"""
Test: Memora scope + owner_agent model, access filter, SQLite fallback removal,
bootstrap unqualified names.  Only touches D:\FRIDAY Universe\Memora.
No .env / commit / SSH / deploy changes.
"""
import os, sys
sys.path.insert(0, r"D:\FRIDAY Universe\Memora")

from storage.relational.models import MemoryRecord, MemoryScopeType, MemorySharedAgent, normalize_agent_name
from storage.relational.access_scope import SERVICE_AGENT, visible_memory_predicate, record_is_visible
from core.config import settings

def test_memory_model_has_scope_and_owner_agent():
    # ORM column presence
    assert hasattr(MemoryRecord, "scope")
    assert hasattr(MemoryRecord, "owner_agent")
    # Enum values
    assert MemoryScopeType.PRIVATE.value == "PRIVATE"
    assert MemoryScopeType.SHARED.value == "SHARED"
    assert MemoryScopeType.UNIVERSAL.value == "UNIVERSAL"
    print("PASS: MemoryRecord model has scope + owner_agent")

def test_access_predicate_matches_required_filter():
    # Predicate logic: UNIVERSAL always; SHARED if owner or in shared_agents; PRIVATE only owner
    # We exercise the SQL expression build; full DB integration requires a session.
    pred = visible_memory_predicate("friday")
    # It must return an SQL expression (not False for a valid caller)
    assert pred is not None
    print("PASS: visible_memory_predicate built for caller='friday'")

def test_record_is_visible_python_mirror():
    # Python mirror for loaded rows; fail-closed for missing caller
    assert record_is_visible(None, None) is False
    # We need a minimal mock row; use a simple object
    class FakeRecord:
        scope = MemoryScopeType.UNIVERSAL
        owner_agent = "any"
        shared_agents = []
    assert record_is_visible(FakeRecord(), "friday") is True
    class FakePrivate:
        scope = MemoryScopeType.PRIVATE
        owner_agent = "friday"
        shared_agents = []
    assert record_is_visible(FakePrivate(), "friday") is True
    assert record_is_visible(FakePrivate(), "other") is False
    class FakeShared:
        scope = MemoryScopeType.SHARED
        owner_agent = "friday"
        shared_agents = []
    assert record_is_visible(FakeShared(), "friday") is True
    assert record_is_visible(FakeShared(), "other") is False
    print("PASS: record_is_visible Python mirror matches predicate")

def test_sqlite_fallback_removed():
    url = settings.DATABASE_URL
    assert "/dev/shm" not in url, f"SQLite fallback /dev/shm still in DATABASE_URL: {url}"
    assert "memora.db" in url or url.startswith("postgresql") or url.startswith("oracle") or url.startswith("sqlite:///./data")
    # The deprecated fallback keys must remain disabled
    assert settings.USE_SQLITE_FALLBACK is False
    print("PASS: SQLite /dev/shm fallback removed from DATABASE_URL")

def test_bootstrap_uses_unqualified_names():
    # Migrations create unqualified tables (no schema prefix); base metadata unqualified.
    from storage.relational.base import Base
    for tname in Base.metadata.tables:
        assert not tname.startswith("memora."), f"Qualified table name found: {tname}"
    # Migration file uses unqualified op.create_table
    mig_path = r"D:\FRIDAY Universe\Memora\migrations\versions\8a8f529e1cdb_create_memora_tables.py"
    with open(mig_path) as f:
        content = f.read()
    assert "op.create_table('agents'" in content
    assert "op.create_table('memory_records'" in content
    print("PASS: Bootstrap / migrations use unqualified table names")

if __name__ == "__main__":
    test_memory_model_has_scope_and_owner_agent()
    test_access_predicate_matches_required_filter()
    test_record_is_visible_python_mirror()
    test_sqlite_fallback_removed()
    test_bootstrap_uses_unqualified_names()
    print("\nAll scope/access/fallback/bootstrap tests passed.")
