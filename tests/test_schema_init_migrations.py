from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from storage.relational import session as relational_session


def _schema_shape(engine):
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names()) - {"alembic_version"}
    columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in table_names
    }
    unique_keys = {}
    for table in table_names:
        keys = {
            tuple(sorted(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints(table)
        }
        keys.update(
            tuple(sorted(index["column_names"]))
            for index in inspector.get_indexes(table)
            if index.get("unique")
        )
        unique_keys[table] = keys
    return table_names, columns, unique_keys


def test_fresh_create_all_database_is_stamped_before_future_upgrades(tmp_path, monkeypatch):
    database_path = tmp_path / "fresh-memora.db"
    engine = create_engine(f"sqlite:///{database_path}")
    migration_only_engine = create_engine(f"sqlite:///{tmp_path / 'migration-only.db'}")
    monkeypatch.setattr(relational_session, "engine", engine)
    monkeypatch.setattr(relational_session, "storage_ready", lambda: True)

    try:
        relational_session.init_db()
        with engine.connect() as connection:
            stamped_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert stamped_revision == "f35ecb0a7c12"

        # The same schema can now pass through the deployment migration command
        # without replaying historical CREATE TABLE/ADD COLUMN operations.
        config = Config("alembic.ini")
        config.attributes["connection"] = engine
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == stamped_revision

        migration_config = Config("alembic.ini")
        migration_config.attributes["connection"] = migration_only_engine
        command.upgrade(migration_config, "head")
        # The current ORM metadata and a clean migration-only install have the
        # same tables, columns, and uniqueness invariants at the stamped head.
        assert _schema_shape(engine) == _schema_shape(migration_only_engine)
    finally:
        engine.dispose()
        migration_only_engine.dispose()
