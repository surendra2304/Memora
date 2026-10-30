"""
Database Engine & Session Management for Memora
Supports PostgreSQL and Oracle Autonomous Database as authoritative stores.

There is no implicit SQLite fallback. If the configured authoritative database
cannot be reached, engine creation raises instead of silently opening a
process-local database that would accept writes and lose them on restart.
SQLite is still supported when it is named explicitly in ``DATABASE_URL`` (local
development and the test suite).
"""
import os
import logging
from pathlib import Path
from typing import Generator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from fastapi import HTTPException
from core.config import settings
from storage.relational.base import Base

# Oracle 23ai JSON Dialect Compatibility Hook
try:
    from sqlalchemy.dialects.oracle.base import OracleDialect
    if not hasattr(OracleDialect, "_json_deserializer"):
        OracleDialect._json_deserializer = None
except Exception:
    pass

logger = logging.getLogger(__name__)
_active_storage_backend = "uninitialized"
_active_storage_durable = False


def production_mode() -> bool:
    return (os.getenv("ENVIRONMENT", "") or settings.MEMORA_ENV).lower() == "production"


def storage_receipt() -> dict[str, str | bool]:
    """Describe the storage actually used by the ORM, not a configured replica."""
    return {
        "backend": _active_storage_backend,
        "durable": _active_storage_durable,
        "durability": "shared_durable" if _active_storage_durable else "process_local",
    }


def validate_production_database_url(url: str) -> None:
    """Fail closed when production is configured with an invalid non-production store."""
    if production_mode() and not url.lower().startswith(("oracle", "oracle+oracledb", "postgresql://", "postgresql+")):
        raise RuntimeError(
            "Memora production requires the self-hosted PostgreSQL DATABASE_URL or Oracle Autonomous Database; "
            "SQLite and Turso are development/migration-only backends."
        )


def storage_ready() -> bool:
    return not production_mode() or _active_storage_durable


def _unavailable(backend: str, exc: Exception) -> RuntimeError:
    """Build the fail-closed error used when an authoritative store is down."""
    return RuntimeError(
        f"Memora {backend} is unavailable; refusing to open a local SQLite fallback. "
        "Fix DATABASE_URL / network access and restart Memora."
    )


def _ensure_sqlite_dir(url: str):
    if "sqlite:///" in url:
        path = url.replace("sqlite:///", "")
        if "?" in path:
            path = path.split("?")[0]
        if path and path != ":memory:":
            dirname = os.path.dirname(os.path.abspath(path))
            if dirname:
                os.makedirs(dirname, exist_ok=True)


def _turso_sqlalchemy_url(url: str) -> str:
    """Convert a Turso URL to the URL accepted by sqlalchemy-libsql."""
    value = url.strip()
    if value.startswith("sqlite+libsql://"):
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "libsql"} or not parsed.netloc:
        raise ValueError("Turso URL must use https:// or libsql:// and include a host")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("secure", "true")
    return urlunsplit(("sqlite+libsql", parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _new_sqlite_engine(url: str):
    _ensure_sqlite_dir(url)

    # Under concurrent load the default journal mode serialises readers against
    # the single writer, and connections give up almost immediately with
    # "database is locked". Verified with scripts/stress_memora.py: 6 agents
    # writing while 6 readers queried produced OperationalError on both the
    # event_log insert and the read path.
    #
    # WAL lets readers proceed concurrently with one writer, and busy_timeout
    # makes a contended connection wait instead of failing. Both are set per
    # connection because SQLite pragmas are not inherited from the file.
    timeout_seconds = float(os.getenv("MEMORA_SQLITE_BUSY_TIMEOUT", "30"))

    engine = create_engine(
        url,
        connect_args={
            "check_same_thread": False,
            "timeout": timeout_seconds,
        } if "sqlite" in url else {},
        echo=settings.DB_ECHO,
    )

    if "sqlite" in url:
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=5000;")
                # WAL's recommended durability/performance trade-off.
                cursor.execute("PRAGMA synchronous=NORMAL;")
            finally:
                cursor.close()

    return engine

def create_db_engine():
    global _active_storage_backend, _active_storage_durable
    db_url = settings.DATABASE_URL
    configured_turso_url = settings.TURSO_DATABASE_URL or os.getenv("TURSO_DATABASE_URL", "")
    turso_token = settings.TURSO_AUTH_TOKEN or os.getenv("TURSO_AUTH_TOKEN", "")

    # Production is self-hosted and PostgreSQL-only. Hosted Turso is never selected
    # implicitly; local development can opt in explicitly if migration work requires it.
    use_turso_primary = os.getenv("MEMORA_USE_TURSO", "").strip().lower() in {"1", "true", "yes"}
    if use_turso_primary and not production_mode() and configured_turso_url and turso_token:
        db_url = configured_turso_url
    validate_production_database_url(db_url)

    # 1. Oracle Autonomous Database 23ai connection
    if db_url.startswith(("oracle", "oracle+oracledb")):
        try:
            pool_size = max(1, min(int(os.getenv("MEMORA_DB_POOL_SIZE", "3")), 8))
            max_overflow = max(0, min(int(os.getenv("MEMORA_DB_MAX_OVERFLOW", "2")), 8))
            engine = create_engine(
                db_url,
                pool_pre_ping=True,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=15,
                echo=settings.DB_ECHO,
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM DUAL"))
            logger.info("Successfully connected to Oracle Autonomous Database 23ai.")
            _active_storage_backend = "oracle"
            _active_storage_durable = True
            return engine
        except Exception as e:
            _active_storage_backend = "unavailable"
            _active_storage_durable = False
            raise _unavailable("Oracle database", e) from e

    # 2. PostgreSQL connection
    if db_url.startswith("postgresql"):
        try:
            # Every FRIDAY specialist owns a small SQLAlchemy pool while sharing one
            # PostgreSQL instance. Do not let default per-service pools add up to
            # hundreds of connections on the 2-OCPU Oracle host.
            pool_size = max(1, min(int(os.getenv("MEMORA_DB_POOL_SIZE", "3")), 8))
            max_overflow = max(0, min(int(os.getenv("MEMORA_DB_MAX_OVERFLOW", "2")), 8))
            engine = create_engine(
                db_url,
                pool_pre_ping=True,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=10,
                echo=settings.DB_ECHO,
                connect_args={"connect_timeout": 3},
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Successfully connected to PostgreSQL database.")
            _active_storage_backend = "postgresql"
            _active_storage_durable = True
            return engine
        except Exception as e:
            # A write must never drift to a process-local database when the
            # canonical PostgreSQL store is unavailable.
            _active_storage_backend = "unavailable"
            _active_storage_durable = False
            raise _unavailable("PostgreSQL database", e) from e

    # 2. Turso cloud database connection through Turso's SQLAlchemy dialect.
    if "turso.io" in db_url or db_url.startswith(("libsql://", "sqlite+libsql://")):
        try:
            if not turso_token:
                raise RuntimeError("TURSO_AUTH_TOKEN is not configured")
            clean_url = _turso_sqlalchemy_url(db_url)
            engine = create_engine(
                clean_url,
                connect_args={"auth_token": turso_token},
                pool_pre_ping=True,
                echo=settings.DB_ECHO,
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Successfully connected to Turso cloud database.")
            _active_storage_backend = "turso"
            _active_storage_durable = True
            return engine
        except Exception as e:
            _active_storage_backend = "unavailable"
            _active_storage_durable = False
            raise _unavailable("Turso database", e) from e

    # 3. Standard SQLite connection
    _active_storage_backend = "sqlite"
    sqlite_authoritative = os.getenv("MEMORA_SQLITE_AUTHORITATIVE", "").lower() in {"1", "true", "yes"}
    _active_storage_durable = sqlite_authoritative
    return _new_sqlite_engine(db_url)

engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def _add_scope_columns(engine, columns: set[str], dialect: str) -> None:
    """Add the scope tier columns to a schema created before they existed.

    ``create_all`` never alters an existing table, so an upgrade from an older
    Memora schema would leave ``memory_records`` without the two columns every
    read path now selects. Add them here (idempotently) rather than requiring a
    manual DBA step. Values are backfilled immediately afterwards.
    """
    statements: list[str] = []
    if "scope" not in columns:
        if dialect == "oracle":
            statements.append(
                "ALTER TABLE memory_records ADD (scope VARCHAR2(9))"
            )
        else:
            statements.append(
                "ALTER TABLE memory_records ADD COLUMN scope VARCHAR(9)"
            )
    if "owner_agent" not in columns:
        if dialect == "oracle":
            statements.append(
                "ALTER TABLE memory_records ADD (owner_agent VARCHAR2(128))"
            )
        else:
            statements.append(
                "ALTER TABLE memory_records ADD COLUMN owner_agent VARCHAR(128)"
            )

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _backfill_scope_columns(engine) -> None:
    """Derive scope/owner for rows that pre-date the columns, then constrain."""
    from storage.relational.models import SHARED_WITH_ALL_AGENTS

    dialect = engine.dialect.name
    with engine.begin() as connection:
        # owner_agent: the owning agent's name, resolved from the FK first and
        # from the identity-scope column as a fallback.
        connection.execute(text(
            "UPDATE memory_records SET owner_agent = ("
            "  SELECT LOWER(a.name) FROM agents a WHERE a.id = memory_records.owner_id"
            ") WHERE owner_agent IS NULL AND EXISTS ("
            "  SELECT 1 FROM agents a WHERE a.id = memory_records.owner_id"
            ")"
        ))
        connection.execute(text(
            "UPDATE memory_records SET owner_agent = LOWER(agent_id) "
            "WHERE owner_agent IS NULL AND agent_id IS NOT NULL"
        ))
        connection.execute(text(
            "UPDATE memory_records SET owner_agent = 'friday' WHERE owner_agent IS NULL"
        ))

        # scope: the namespace type already encoded the historical visibility.
        connection.execute(text(
            "UPDATE memory_records SET scope = 'UNIVERSAL' WHERE scope IS NULL "
            "AND namespace_id IN ("
            "  SELECT id FROM namespaces WHERE type IN ('universe-global', 'public')"
            ")"
        ))
        connection.execute(text(
            "UPDATE memory_records SET scope = 'SHARED' WHERE scope IS NULL "
            "AND namespace_id IN ("
            "  SELECT id FROM namespaces WHERE type = 'team-shared'"
            ")"
        ))
        connection.execute(text(
            "UPDATE memory_records SET scope = 'PRIVATE' WHERE scope IS NULL"
        ))

        # SHARED rows written under an open team namespace keep their historical
        # meaning: readable by every agent in the tenant.
        open_shared = [
            row[0]
            for row in connection.execute(text(
                "SELECT m.id FROM memory_records m "
                "JOIN namespaces n ON n.id = m.namespace_id "
                "WHERE m.scope = 'SHARED' AND n.type = 'team-shared'"
            )).fetchall()
        ]
        already_listed = {
            row[0]
            for row in connection.execute(text(
                "SELECT DISTINCT memory_id FROM memory_shared_agents"
            )).fetchall()
        }
        missing = [memory_id for memory_id in open_shared if memory_id not in already_listed]
        if missing:
            for memory_id in missing:
                connection.execute(
                    text(
                        "INSERT INTO memory_shared_agents "
                        "(id, tenant_id, memory_id, agent_name, granted_by, created_at) "
                        "SELECT :id, m.tenant_id, m.id, :agent_name, :granted_by, CURRENT_TIMESTAMP "
                        "FROM memory_records m WHERE m.id = :memory_id"
                    ),
                    {
                        "id": __import__("uuid").uuid4().hex,
                        "memory_id": memory_id,
                        "agent_name": SHARED_WITH_ALL_AGENTS,
                        "granted_by": "memora",
                    },
                )

    # Enforce NOT NULL where the dialect can do it in place. SQLite cannot
    # modify a column, and in any case the backfill above leaves no NULLs.
    if dialect in {"oracle", "postgresql"}:
        with engine.begin() as connection:
            for column in ("scope", "owner_agent"):
                try:
                    if dialect == "oracle":
                        connection.execute(text(
                            f"ALTER TABLE memory_records MODIFY ({column} NOT NULL)"
                        ))
                    else:
                        connection.execute(text(
                            f"ALTER TABLE memory_records ALTER COLUMN {column} SET NOT NULL"
                        ))
                except Exception:
                    # Oracle raises ORA-01442 when the column is already NOT NULL.
                    logger.debug("NOT NULL already enforced for memory_records.%s", column)


def _ensure_scope_indexes(engine, tables: set[str]) -> None:
    """Create the (scope, owner_agent) index on pre-existing schemas."""
    if "memory_records" not in tables:
        return
    indexes = {
        (index["name"] or "").lower()
        for index in inspect(engine).get_indexes("memory_records")
    }
    if "ix_memory_scope_owner" in indexes:
        return

    dialect = engine.dialect.name
    if dialect == "oracle":
        ddl = """
        BEGIN
            EXECUTE IMMEDIATE 'CREATE INDEX ix_memory_scope_owner ON memory_records (scope, owner_agent)';
        EXCEPTION
            WHEN OTHERS THEN
                IF SQLCODE != -955 THEN RAISE; END IF;
        END;
        """
        with engine.begin() as connection:
            connection.execute(text(ddl))
    else:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_memory_scope_owner "
                "ON memory_records (scope, owner_agent)"
            ))


def _ensure_scope_constraint(engine, tables: set[str]) -> None:
    """Add the scope CHECK constraint where the dialect supports it in place."""
    if "memory_records" not in tables or engine.dialect.name != "oracle":
        return
    ddl = """
    BEGIN
        EXECUTE IMMEDIATE 'ALTER TABLE memory_records ADD CONSTRAINT ck_memory_scope CHECK (scope IN (''PRIVATE'', ''SHARED'', ''UNIVERSAL''))';
    EXCEPTION
        WHEN OTHERS THEN
            IF SQLCODE NOT IN (-2264, -2260) THEN RAISE; END IF;
    END;
    """
    with engine.begin() as connection:
        connection.execute(text(ddl))


def init_db():
    """Initialize a fresh ORM schema without leaving it untracked by Alembic."""
    if not storage_ready():
        logger.error("Skipping local schema initialization: production storage is not durable and authoritative.")
        return

    # Ensure all mapped classes are registered before comparing the database to
    # metadata; importing this module alone does not register them.
    __import__("storage.relational.models")
    existing_tables = set(inspect(engine).get_table_names())
    has_memora_tables = bool(existing_tables & set(Base.metadata.tables))
    has_alembic_version = "alembic_version" in existing_tables
    Base.metadata.create_all(bind=engine)

    # A schema created before the scope tier existed needs the new columns and
    # their values before any ORM query selects them. `create_all` above only
    # created the new `memory_shared_agents` table.
    if "memory_records" in existing_tables:
        dialect = engine.dialect.name
        columns = {
            column["name"].lower()
            for column in inspect(engine).get_columns("memory_records")
        }
        added = bool({"scope", "owner_agent"} - columns)
        if added:
            _add_scope_columns(engine, columns, dialect)
        # Backfill is idempotent: it only touches NULL values, so running it on
        # every boot costs one indexed UPDATE.
        _backfill_scope_columns(engine)
        _ensure_scope_indexes(engine, existing_tables)
        _ensure_scope_constraint(engine, existing_tables)

    if not has_memora_tables and not has_alembic_version:
        # A brand-new database has no data migrations to replay. `create_all`
        # built its schema from the current ORM metadata, so record that exact
        # starting point. Otherwise a later `alembic upgrade head` would replay
        # historical CREATE TABLE operations over tables that already exist.
        from alembic import command
        from alembic.config import Config

        config_path = Path(__file__).resolve().parents[2] / "migrations" / "alembic.ini"
        if not config_path.is_file():
            raise RuntimeError("Packaged Alembic configuration is missing")
        config = Config(str(config_path))
        config.attributes["connection"] = engine
        command.stamp(config, "head")
    elif has_memora_tables and not has_alembic_version:
        # Existing unversioned schemas may need historical data transforms;
        # never auto-stamp them based on table names alone.
        logger.warning(
            "Existing Memora tables have no Alembic version; refusing to auto-stamp an unverified schema."
        )

    # Bootstrap the vector store. On Oracle this creates the native
    # VECTOR(384, FLOAT32) table plus its HNSW neighbour-graph index; elsewhere
    # it creates the portable emulation table used by tests and local dev.
    try:
        from storage.vector.oracle_vector_adapter import oracle_vector_adapter

        oracle_vector_adapter.ensure_schema()
    except Exception as exc:  # pragma: no cover - defensive bootstrap
        logger.warning("Vector store bootstrap skipped: %s", exc)

def get_db() -> Generator[Session, None, None]:
    """Dependency for obtaining a database session."""
    if not storage_ready():
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Memora production storage is unavailable: configure a supported durable authoritative database.",
                **storage_receipt(),
            },
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def checkpoint_wal():
    """Checkpoint SQLite WAL log to ensure all writes are flushed to disk before exit."""
    try:
        if "sqlite" in str(engine.url):
            with engine.connect() as conn:
                conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE);"))
            logger.info("SQLite WAL checkpoint completed successfully.")
    except Exception as exc:
        logger.warning("SQLite WAL checkpoint failed: %s", exc)

