"""Safely provision the VyralX Memora identity and private namespace.

Unlike the broad FRIDAY seed script, this one-shot operation never deletes or
rewrites existing memory records. It fails closed if the VyralX key is missing
or if the private namespace is already owned by another identity.
"""
from __future__ import annotations

import os

from core.identity.service import IdentityService
from storage.relational.models import NamespaceType
from storage.relational.session import SessionLocal, init_db, storage_ready


def register_vyralx_identity() -> tuple[str, str]:
    """Ensure the VyralX worker and its private namespace exist in the default tenant."""
    api_key = os.environ.get("VYRALX_API_KEY", "").strip()
    if len(api_key) < 32:
        raise RuntimeError(
            "Set a unique VYRALX_API_KEY of at least 32 characters before provisioning VyralX."
        )

    init_db()
    if not storage_ready():
        raise RuntimeError(
            "Memora storage is not ready and authoritative; refusing to provision the VyralX identity."
        )
    db = SessionLocal()
    try:
        agent = IdentityService.register_agent(
            db,
            name="vyralx",
            description="VyralX video production and reviewed publishing agent",
            role="worker",
            tenant_id="default",
        )
        namespace = IdentityService.resolve_namespace(
            db,
            path="memora://vyralx/private",
            default_type=NamespaceType.AGENT_PRIVATE,
            owner_agent_id=agent.id,
            tenant_id=agent.tenant_id,
        )

        if namespace.agent_id != agent.id:
            raise RuntimeError(
                "The namespace memora://vyralx/private is not owned by the VyralX identity; "
                "resolve the ownership conflict manually before enabling Memora access."
            )
        namespace_type = getattr(namespace.type, "value", namespace.type)
        if namespace_type != NamespaceType.AGENT_PRIVATE.value:
            raise RuntimeError(
                "The namespace memora://vyralx/private is not an agent-private namespace; "
                "resolve the namespace-type conflict manually before enabling Memora access."
            )

        print(
            "VyralX identity ready; private namespace verified. "
            "No existing memory records were deleted or rewritten."
        )
        return agent.id, namespace.path
    finally:
        db.close()


if __name__ == "__main__":
    register_vyralx_identity()
