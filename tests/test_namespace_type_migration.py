"""Data migration guards for namespaces previously promoted by substring matching."""
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.policy.engine import PolicyEngine
from storage.relational.base import Base
from storage.relational.models import Agent, Namespace, NamespaceType


def test_upgrade_closes_noncanonical_public_and_global_namespace_rows():
    engine = create_engine("sqlite:///:memory:")
    rows = [
        ("personal-public", "memora://friday/projects/publicity", "public", "friday-id"),
        ("personal-global", "memora://friday/projects/global", "universe-global", "friday-id"),
        ("personal-globalized", "memora://friday/projects/globalized", "universe-global", "friday-id"),
        ("canonical-public", "memora://public/announcements", "public", None),
        ("canonical-global", "memora://universe/global", "universe-global", None),
        ("shared-root", "memora://team/shared", "public", None),
        ("private-root", "memora://friday/private", "universe-global", "friday-id"),
    ]
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE namespaces (id TEXT PRIMARY KEY, path TEXT NOT NULL, type TEXT NOT NULL, agent_id TEXT)")
        )
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('f35ecb0a7c12')")
        )
        connection.execute(
            text("INSERT INTO namespaces (id, path, type, agent_id) VALUES (:id, :path, :type, :agent_id)"),
            [
                {"id": row_id, "path": path, "type": namespace_type, "agent_id": owner_id}
                for row_id, path, namespace_type, owner_id in rows
            ],
        )

    config = Config("alembic.ini")
    config.attributes["connection"] = engine
    command.upgrade(config, "head")

    with engine.connect() as connection:
        migrated = dict(connection.execute(text("SELECT id, type FROM namespaces")).all())
        owner_id = connection.execute(
            text("SELECT agent_id FROM namespaces WHERE id = 'personal-public'")
        ).scalar_one()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert migrated == {
        "personal-public": "project-private",
        "personal-global": "project-private",
        "personal-globalized": "project-private",
        "canonical-public": "public",
        "canonical-global": "universe-global",
        "shared-root": "team-shared",
        "private-root": "agent-private",
    }
    assert owner_id == "friday-id"
    assert revision == "b7c24f91e8d3"
    engine.dispose()


def test_migrated_personal_namespaces_are_not_open_to_other_agents(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-namespaces.db'}")
    Base.metadata.create_all(engine)
    stale_namespaces = [
        Namespace(
            id="legacy-public",
            tenant_id="default",
            path="memora://friday/projects/publicity",
            agent_id="friday-id",
            type=NamespaceType.PUBLIC,
        ),
        Namespace(
            id="legacy-global",
            tenant_id="default",
            path="memora://friday/projects/globalized",
            agent_id="friday-id",
            type=NamespaceType.UNIVERSE_GLOBAL,
        ),
    ]
    with Session(engine) as db:
        db.add_all(
            [
                Agent(id="friday-id", tenant_id="default", name="friday", role="worker"),
                Agent(id="forge-id", tenant_id="default", name="forge", role="worker"),
                *stale_namespaces,
            ]
        )
        db.commit()

    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('f35ecb0a7c12')")
        )
    config = Config("alembic.ini")
    config.attributes["connection"] = engine
    command.upgrade(config, "head")

    with Session(engine) as db:
        outsider = db.query(Agent).filter_by(id="forge-id").one()
        migrated = db.query(Namespace).filter(Namespace.id.in_(["legacy-public", "legacy-global"])).all()
        assert {namespace.type for namespace in migrated} == {NamespaceType.PROJECT_PRIVATE}
        assert all(
            not PolicyEngine.evaluate_access(
                db, actor=outsider, namespace=namespace, action="read", log_audit=False
            ).allowed
            for namespace in migrated
        )
    engine.dispose()
