"""
Oracle Autonomous Database 23ai Native Vector Store Adapter for Memora.
Leverages Oracle 23ai native VECTOR datatypes, COSINE vector distance,
and HNSW in-memory neighbor graph vector indexing for enterprise-grade memory retrieval.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from storage.relational.session import SessionLocal, storage_receipt
from storage.vector.qdrant_adapter import VectorSearchResult

logger = logging.getLogger(__name__)


class OracleVectorAdapter:
    """
    Native vector adapter for Oracle Autonomous Database 23ai.
    Utilizes VECTOR(384, FLOAT32) and native VECTOR_DISTANCE(..., COSINE).
    Falls back to JSON vector storage with in-process cosine computation when running on SQLite/local tests.
    """

    def __init__(self, table_name: str = "memora_vector_store", dimension: int = 384):
        self.table_name = table_name
        self.dimension = dimension
        self._schema_initialized = False

    def is_oracle_backend(self) -> bool:
        receipt = storage_receipt()
        return receipt.get("backend") == "oracle"

    def ensure_schema(self, db: Optional[Session] = None) -> None:
        """Create or verify the vector store table and index."""
        if self._schema_initialized:
            return

        session = db or SessionLocal()
        should_close = db is None

        try:
            if self.is_oracle_backend():
                # Oracle 23ai native VECTOR table DDL
                table_ddl = f"""
                BEGIN
                    EXECUTE IMMEDIATE '
                        CREATE TABLE {self.table_name} (
                            id VARCHAR2(64) PRIMARY KEY,
                            tenant_id VARCHAR2(64) DEFAULT ''default'' NOT NULL,
                            embedding VECTOR({self.dimension}, FLOAT32),
                            payload CLOB,
                            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                        )
                    ';
                EXCEPTION
                    WHEN OTHERS THEN
                        IF SQLCODE != -955 THEN RAISE; END IF;
                END;
                """
                session.execute(text(table_ddl))

                # Oracle 23ai HNSW Vector Index DDL
                index_ddl = f"""
                BEGIN
                    EXECUTE IMMEDIATE '
                        CREATE VECTOR INDEX {self.table_name}_idx ON {self.table_name} (embedding)
                        ORGANIZATION INMEMORY NEIGHBOR GRAPH
                        DISTANCE COSINE
                    ';
                EXCEPTION
                    WHEN OTHERS THEN
                        IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
                END;
                """
                session.execute(text(index_ddl))
                session.commit()
                logger.info("Oracle 23ai native VECTOR table and HNSW index verified: %s", self.table_name)
            else:
                # SQLite / PostgreSQL compatibility emulation table
                session.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS {self.table_name} (
                        id VARCHAR(64) PRIMARY KEY,
                        tenant_id VARCHAR(64) DEFAULT 'default' NOT NULL,
                        embedding TEXT NOT NULL,
                        payload TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                session.commit()
            self._schema_initialized = True
        except Exception as exc:
            session.rollback()
            logger.warning("Could not initialize vector table %s: %s", self.table_name, exc)
        finally:
            if should_close:
                session.close()

    def upsert_embedding(
        self,
        memory_id: str,
        vector: List[float],
        payload: Optional[Dict[str, Any]] = None,
        tenant_id: str = "default",
        db: Optional[Session] = None,
    ) -> bool:
        """Upsert a dense vector embedding."""
        if not vector:
            return False

        self.ensure_schema(db)
        session = db or SessionLocal()
        should_close = db is None

        try:
            payload_str = json.dumps(payload or {})
            if self.is_oracle_backend():
                vector_str = f"[{','.join(str(round(v, 6)) for v in vector)}]"
                sql = f"""
                MERGE INTO {self.table_name} dest
                USING (SELECT :id AS id, :tenant_id AS tenant_id, TO_VECTOR(:vector) AS embedding, :payload AS payload FROM DUAL) src
                ON (dest.id = src.id)
                WHEN MATCHED THEN
                    UPDATE SET dest.embedding = src.embedding, dest.payload = src.payload, dest.updated_at = CURRENT_TIMESTAMP
                WHEN NOT MATCHED THEN
                    INSERT (id, tenant_id, embedding, payload, updated_at)
                    VALUES (src.id, src.tenant_id, src.embedding, src.payload, CURRENT_TIMESTAMP)
                """
                session.execute(text(sql), {
                    "id": str(memory_id),
                    "tenant_id": str(tenant_id),
                    "vector": vector_str,
                    "payload": payload_str,
                })
            else:
                # Portable upsert for test/fallback environments
                vector_str = json.dumps([round(float(v), 6) for v in vector])
                check_existing = session.execute(
                    text(f"SELECT id FROM {self.table_name} WHERE id = :id"),
                    {"id": str(memory_id)},
                ).scalar()

                if check_existing:
                    session.execute(
                        text(f"UPDATE {self.table_name} SET embedding = :emb, payload = :p, tenant_id = :t WHERE id = :id"),
                        {"id": str(memory_id), "emb": vector_str, "p": payload_str, "t": str(tenant_id)},
                    )
                else:
                    session.execute(
                        text(f"INSERT INTO {self.table_name} (id, tenant_id, embedding, payload) VALUES (:id, :t, :emb, :p)"),
                        {"id": str(memory_id), "t": str(tenant_id), "emb": vector_str, "p": payload_str},
                    )

            session.commit()
            return True
        except Exception as exc:
            session.rollback()
            logger.warning("Vector upsert failed for %s: %s", memory_id, exc)
            return False
        finally:
            if should_close:
                session.close()

    def delete_embedding(
        self,
        memory_id: str,
        tenant_id: str = "default",
        db: Optional[Session] = None,
    ) -> bool:
        """Delete an embedding by memory ID and tenant."""
        self.ensure_schema(db)
        session = db or SessionLocal()
        should_close = db is None

        try:
            session.execute(
                text(f"DELETE FROM {self.table_name} WHERE id = :id AND tenant_id = :tenant_id"),
                {"id": str(memory_id), "tenant_id": str(tenant_id)},
            )
            session.commit()
            return True
        except Exception as exc:
            session.rollback()
            logger.warning("Vector deletion failed for %s: %s", memory_id, exc)
            return False
        finally:
            if should_close:
                session.close()

    def search_similarity(
        self,
        query_vector: List[float],
        limit: int = 10,
        tenant_id: str = "default",
        score_threshold: float = 0.3,
        db: Optional[Session] = None,
    ) -> List[VectorSearchResult]:
        """Search similar embeddings using native Oracle 23ai VECTOR_DISTANCE or cosine ranking."""
        if not query_vector:
            return []

        self.ensure_schema(db)
        session = db or SessionLocal()
        should_close = db is None

        try:
            if self.is_oracle_backend():
                vector_str = f"[{','.join(str(round(v, 6)) for v in query_vector)}]"
                sql = f"""
                SELECT id, payload, (1 - VECTOR_DISTANCE(embedding, TO_VECTOR(:query_vec), COSINE)) AS score
                FROM {self.table_name}
                WHERE tenant_id = :tenant_id
                ORDER BY score DESC
                FETCH FIRST :limit ROWS ONLY
                """
                rows = session.execute(text(sql), {
                    "query_vec": vector_str,
                    "tenant_id": str(tenant_id),
                    "limit": int(limit),
                }).fetchall()

                results = []
                for row in rows:
                    mem_id, raw_payload, score = row[0], row[1], float(row[2])
                    if score >= score_threshold:
                        p_dict = json.loads(raw_payload) if isinstance(raw_payload, str) else (raw_payload or {})
                        results.append(VectorSearchResult(memory_id=str(mem_id), score=score, payload=p_dict))
                return results
            else:
                # Portable in-process cosine distance calculation
                rows = session.execute(
                    text(f"SELECT id, embedding, payload FROM {self.table_name} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": str(tenant_id)},
                ).fetchall()

                scored = []
                q_norm = math.sqrt(sum(qv * qv for qv in query_vector))
                if q_norm == 0:
                    return []

                for row in rows:
                    mem_id = row[0]
                    v_list = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                    p_dict = json.loads(row[2]) if isinstance(row[2], str) else (row[2] or {})

                    v_norm = math.sqrt(sum(x * x for x in v_list))
                    if v_norm > 0:
                        dot = sum(a * b for a, b in zip(query_vector, v_list, strict=False))
                        cosine_sim = dot / (q_norm * v_norm)
                        if cosine_sim >= score_threshold:
                            scored.append(VectorSearchResult(memory_id=str(mem_id), score=round(cosine_sim, 4), payload=p_dict))

                scored.sort(key=lambda x: x.score, reverse=True)
                return scored[:limit]
        except Exception as exc:
            logger.warning("Vector similarity search failed: %s", exc)
            return []
        finally:
            if should_close:
                session.close()

    def readiness(self) -> Dict[str, Any]:
        """Describe vector store readiness for health checks."""
        backend_name = "oracle_23ai_native_vector" if self.is_oracle_backend() else "sqlite_vector_emulation"
        return {
            "status": "available",
            "available": True,
            "backend": backend_name,
            "dimension": self.dimension,
            "table": self.table_name,
        }


oracle_vector_adapter = OracleVectorAdapter()
