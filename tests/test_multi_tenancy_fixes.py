"""
Regression tests for HIGH-6: multi-tenancy was advertised but not enforced.

`agents.name` and `namespaces.path` carried global single-column UNIQUE
constraints while IdentityService looked both up by (name, tenant_id) and
(path, tenant_id). A second tenant registering an agent name or namespace path
the first already used raised IntegrityError instead of succeeding.

Also covers the tenant-scoping added to get_agent_by_name and revoke_access,
which previously resolved across tenant boundaries.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.identity.service import IdentityService
from storage.relational.base import Base
from storage.relational.models import AccessGrant


@pytest.fixture
def fresh_db():
    """A database built straight from the model metadata (the create_all path)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_two_tenants_can_register_the_same_agent_name(fresh_db):
    """The core tenancy bug: this used to raise IntegrityError."""
    acme = IdentityService.register_agent(fresh_db, "friday", tenant_id="acme")
    globex = IdentityService.register_agent(fresh_db, "friday", tenant_id="globex")

    assert acme.id != globex.id
    assert acme.tenant_id == "acme"
    assert globex.tenant_id == "globex"


def test_two_tenants_can_use_the_same_namespace_path(fresh_db):
    """namespaces.path had the same global-unique defect."""
    IdentityService.register_agent(fresh_db, "friday", tenant_id="acme")
    IdentityService.register_agent(fresh_db, "friday", tenant_id="globex")

    ns_acme = IdentityService.resolve_namespace(
        fresh_db, "memora://friday/private", tenant_id="acme"
    )
    ns_globex = IdentityService.resolve_namespace(
        fresh_db, "memora://friday/private", tenant_id="globex"
    )

    assert ns_acme.id != ns_globex.id
    assert ns_acme.tenant_id == "acme"
    assert ns_globex.tenant_id == "globex"


def test_a_duplicate_name_inside_one_tenant_is_still_idempotent(fresh_db):
    """Relaxing the constraint must not create duplicate agents in a tenant."""
    first = IdentityService.register_agent(fresh_db, "forge", tenant_id="acme")
    second = IdentityService.register_agent(fresh_db, "forge", tenant_id="acme")
    assert first.id == second.id


def test_agent_lookup_can_be_scoped_to_a_tenant(fresh_db):
    IdentityService.register_agent(fresh_db, "friday", tenant_id="acme")
    globex = IdentityService.register_agent(fresh_db, "friday", tenant_id="globex")

    assert IdentityService.get_agent_by_name(fresh_db, "friday", tenant_id="globex").id == globex.id
    assert (
        IdentityService.get_agent_by_name(fresh_db, "friday", tenant_id="acme").tenant_id == "acme"
    )
    assert IdentityService.get_agent_by_name(fresh_db, "friday", tenant_id="nope") is None


def test_unscoped_lookup_still_works_for_existing_callers(fresh_db):
    """25 call sites pass no tenant_id; they must keep working."""
    agent = IdentityService.register_agent(fresh_db, "nexus", tenant_id="acme")
    assert IdentityService.get_agent_by_name(fresh_db, "nexus").id == agent.id


def test_revoke_access_can_be_scoped_to_a_tenant(fresh_db):
    """A caller must not be able to revoke another tenant's grant."""
    IdentityService.register_agent(fresh_db, "friday", tenant_id="acme")
    IdentityService.register_agent(fresh_db, "intelx", tenant_id="acme")
    IdentityService.register_agent(fresh_db, "intelx", tenant_id="globex")
    namespace = IdentityService.resolve_namespace(
        fresh_db, "memora://friday/private", tenant_id="acme"
    )

    acme_intelx = IdentityService.get_agent_by_name(fresh_db, "intelx", tenant_id="acme")
    IdentityService.grant_access(
        fresh_db,
        agent_name="intelx",
        namespace_id=namespace.id,
        actions=["read"],
        tenant_id="acme",
    )

    # Wrong tenant: nothing to revoke.
    globex_intelx = IdentityService.get_agent_by_name(fresh_db, "intelx", tenant_id="globex")
    assert (
        IdentityService.revoke_access(
            fresh_db,
            agent_id=globex_intelx.id,
            namespace_id=namespace.id,
            tenant_id="globex",
        )
        is False
    )

    # Right tenant: revoked.
    assert (
        IdentityService.revoke_access(
            fresh_db,
            agent_id=acme_intelx.id,
            namespace_id=namespace.id,
            tenant_id="acme",
        )
        is True
    )
    remaining = (
        fresh_db.query(AccessGrant).filter(AccessGrant.agent_id == acme_intelx.id).count()
    )
    assert remaining == 0


def test_revoke_access_without_a_tenant_still_works(fresh_db):
    IdentityService.register_agent(fresh_db, "friday")
    IdentityService.register_agent(fresh_db, "forge")
    namespace = IdentityService.resolve_namespace(fresh_db, "memora://friday/private")
    agent = IdentityService.get_agent_by_name(fresh_db, "forge")
    IdentityService.grant_access(
        fresh_db, agent_name="forge", namespace_id=namespace.id, actions=["read"]
    )
    assert IdentityService.revoke_access(fresh_db, agent.id, namespace.id) is True


def test_the_model_declares_composite_uniqueness():
    """Guard the schema itself, not just the service layer."""
    from storage.relational.models import Agent, Namespace

    agent_cols = {
        tuple(sorted(constraint.columns.keys()))
        for constraint in Agent.__table__.constraints
        if type(constraint).__name__ == "UniqueConstraint"
    }
    namespace_cols = {
        tuple(sorted(constraint.columns.keys()))
        for constraint in Namespace.__table__.constraints
        if type(constraint).__name__ == "UniqueConstraint"
    }

    assert ("name", "tenant_id") in agent_cols, f"agents composite uniqueness missing: {agent_cols}"
    assert ("path", "tenant_id") in namespace_cols, (
        f"namespaces composite uniqueness missing: {namespace_cols}"
    )

    # And the single-column global unique must be gone.
    assert Agent.__table__.c.name.unique is not True
    assert Namespace.__table__.c.path.unique is not True
