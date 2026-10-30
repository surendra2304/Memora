# Memora Scope / Owner-Agent / Access Filter / Fallback Removal — Report

**Scope:** Only `D:\FRIDAY Universe\Memora`. No `.env` / commit / SSH / deploy files modified.

---

## 1. Memory Model (scope + owner_agent)

**File:** `core/memory/schemas.py` (Pydantic) + `storage/relational/models.py` (ORM)

- `MemoryRecordCreate` now exposes `owner_agent: Optional[str]` (line ~101) alongside existing `scope: Optional[MemoryScopeType]` (line 108) and `shared_with: Optional[List[str]]` (line 112).
- `MemoryRecordRead` now exposes `owner_agent: str = "friday"`, `scope: MemoryScopeType = MemoryScopeType.PRIVATE`, and `shared_with: Optional[List[str]] = None`.
- `MemoryRecordUpdate` accepts `scope` and `shared_with` for mutations.
- ORM `MemoryRecord` (`storage/relational/models.py`, lines 233-238) already has:
  - `scope: Mapped[MemoryScopeType]` (nullable=False, default=PRIVATE)
  - `owner_agent: Mapped[str]` (nullable=False, default="friday")
  - `CheckConstraint("scope IN ('PRIVATE', 'SHARED', 'UNIVERSAL')", name="ck_memory_scope")`
- `MemorySharedAgent` join table exists for SHARED membership (`memory_shared_agents`).
- `MemoryScopeType` enum (`storage/relational/models.py`, lines 33-50) defines PRIVATE / SHARED / UNIVERSAL.

---

## 2. Access Predicate (enforced in retrieval)

**File:** `storage/relational/access_scope.py`

The required filter — `scope=UNIVERSAL OR (SHARED allowed) OR (PRIVATE owner)` — is implemented by:

```python
def visible_memory_predicate(caller_agent: Optional[str]):
    caller = normalize_agent_name(caller_agent)
    if not caller:
        return false()  # fail-closed
    shared_with_caller = exists().where(
        and_(
            MemorySharedAgent.memory_id == MemoryRecord.id,
            MemorySharedAgent.agent_name.in_((caller, SHARED_WITH_ALL_AGENTS)),
        )
    )
    return or_(
        MemoryRecord.scope == MemoryScopeType.UNIVERSAL,
        and_(
            MemoryRecord.scope == MemoryScopeType.SHARED,
            or_(MemoryRecord.owner_agent == caller, shared_with_caller),
        ),
        and_(
            MemoryRecord.scope == MemoryScopeType.PRIVATE,
            MemoryRecord.owner_agent == caller,
        ),
    )
```

**Enforced in retrieval paths:**
- `core/memory/search_service.py` lines 170 (keyword leg) and 272 (union / RRF leg) apply `visible_memory_predicate(actor.name)`.
- `core/memory/service.py` line 446 applies `visible_memory_predicate(actor.name)` in `query_memory()` / list paths.
- `record_is_visible()` (line 85) is the Python mirror for loaded rows.
- Every helper fails closed (missing caller → false / denied).

---

## 3. SQLite /dev/shm Fallback Removed

**File:** `core/config.py`

- Before: `DATABASE_URL = "sqlite:////dev/shm/memora.db" if os.path.exists("/dev/shm") else "sqlite:///./data/memora.db"`
- After: `DATABASE_URL = "sqlite:///./data/memora.db"`
- Deprecated keys `SQLITE_FALLBACK_URL = None` and `USE_SQLITE_FALLBACK = False` preserved (already disabled); no fallback path remains.
- `storage/relational/session.py` already states: "There is no implicit SQLite fallback. If the configured authoritative database cannot be reached, engine creation raises instead of silently opening a process-local database."

---

## 4. Bootstrap in MEMORA Schema (unqualified names)

**Method:** Alembic migrations (`migrations/versions/8a8f529e1cdb_create_memora_tables.py`) and ORM `Base.metadata`.

- All `op.create_table('agents'...)` / `op.create_table('memory_records'...)` / `op.create_table('namespaces'...)` use unqualified names (`schema=None` in `batch_alter_table`).
- `Base.metadata.tables` keys contain no `memora.` prefix.
- `memory_shared_agents` and `memory_scope_type` enum created unqualified.
- Seed script (`scripts/seed_memora_identity.py`) uses unqualified agent handles (`friday`, `forge`, etc.), not schema-qualified IDs.

---

## 5. Tests Created (only in D:\FRIDAY Universe\Memora)

**File:** `tests/test_scope_access_and_fallback_removal.py`

Tests executed successfully (all PASS):
- `test_memory_model_has_scope_and_owner_agent` — ORM columns + enum.
- `test_access_predicate_matches_required_filter` — SQL expression builds.
- `test_record_is_visible_python_mirror` — PRIVATE (owner only), SHARED (owner / shared list), UNIVERSAL (always), missing caller (closed).
- `test_sqlite_fallback_removed` — `/dev/shm` absent from `DATABASE_URL`; `USE_SQLITE_FALLBACK` False.
- `test_bootstrap_uses_unqualified_names` — no `memora.` in table names; migration uses `op.create_table('...')` unqualified.

---

## 6. Nothing Outside Memora Modified

- No `.env`, `.env.example`, `.git` objects, commit metadata, SSH keys, or deploy scripts (`docker-compose.yml`, `Dockerfile` outside Memora untouched).
- Only edited: `core/memory/schemas.py`, `core/config.py`, added `tests/test_scope_access_and_fallback_removal.py`, wrote `MEMORA_SCOPE_ACCESS_REPORT.md`.

---

**Access Predicate (single line):**
`scope=UNIVERSAL OR (scope=SHARED AND (owner_agent=caller OR caller IN shared_agents)) OR (scope=PRIVATE AND owner_agent=caller)` — enforced via `visible_memory_predicate()` in all SQL retrieval paths and mirrored in `record_is_visible()`.
