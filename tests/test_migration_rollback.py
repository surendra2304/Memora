"""
Migration Rollback and Forward Verification Test for Memora.
Tests that Alembic migration 5e89a1b2c3d4 (identity scope & idempotency)
can cleanly downgrade to 433bb01f2a2a and upgrade back to head.
"""
import os
import tempfile
import sqlite3
import pytest
from alembic.config import Config
from alembic import command


def test_alembic_migration_upgrade_and_rollback():
    # Create isolated temp database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db_path = tf.name

    try:
        # Prepare alembic config pointing to temp db
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{temp_db_path.replace(os.sep, '/')}")

        # 1. Upgrade to head (5e89a1b2c3d4)
        command.upgrade(alembic_cfg, "head")

        # Verify columns exist in temp db
        conn = sqlite3.connect(temp_db_path)
        c = conn.cursor()
        c.execute("PRAGMA table_info(memory_records)")
        cols_upgraded = [r[1] for r in c.fetchall()]
        conn.close()

        assert "user_id" in cols_upgraded
        assert "agent_id" in cols_upgraded
        assert "workspace_id" in cols_upgraded
        assert "device_id" in cols_upgraded
        assert "task_id" in cols_upgraded
        assert "idempotency_key" in cols_upgraded

        # 2. Downgrade to 433bb01f2a2a (previous head)
        command.downgrade(alembic_cfg, "433bb01f2a2a")

        # Verify columns were removed on downgrade
        conn = sqlite3.connect(temp_db_path)
        c = conn.cursor()
        c.execute("PRAGMA table_info(memory_records)")
        cols_downgraded = [r[1] for r in c.fetchall()]
        conn.close()

        assert "user_id" not in cols_downgraded
        assert "idempotency_key" not in cols_downgraded

        # 3. Upgrade back to head
        command.upgrade(alembic_cfg, "head")

        conn = sqlite3.connect(temp_db_path)
        c = conn.cursor()
        c.execute("PRAGMA table_info(memory_records)")
        cols_reupgraded = [r[1] for r in c.fetchall()]
        conn.close()

        assert "user_id" in cols_reupgraded
        assert "idempotency_key" in cols_reupgraded

    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass
