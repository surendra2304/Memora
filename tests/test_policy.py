"""
Tests for Namespace Policies and Access Boundaries
"""
import pytest
from storage.relational.models import Agent, Namespace, NamespaceType
from core.policy.engine import PolicyEngine

def test_policy_boundaries(test_db):
    agent_a = Agent(name="agent_a")
    agent_b = Agent(name="agent_b")
    supervisor = Agent(name="friday")
    test_db.add_all([agent_a, agent_b, supervisor])
    test_db.commit()

    private_ns = Namespace(path="memora://agent_a/private", type=NamespaceType.AGENT_PRIVATE, agent_id=agent_a.id)
    shared_ns = Namespace(path="memora://universe/global", type=NamespaceType.UNIVERSE_GLOBAL)
    test_db.add_all([private_ns, shared_ns])
    test_db.commit()

    # Owner can read/write private namespace
    assert PolicyEngine.evaluate_access(test_db, agent_a, private_ns, "write").allowed is True
    assert PolicyEngine.evaluate_access(test_db, agent_a, private_ns, "read").allowed is True

    # Other agent is rejected on private namespace
    assert PolicyEngine.evaluate_access(test_db, agent_b, private_ns, "read").allowed is False
    assert PolicyEngine.evaluate_access(test_db, agent_b, private_ns, "write").allowed is False

    # Supervisor (friday) can access private namespace
    assert PolicyEngine.evaluate_access(test_db, supervisor, private_ns, "read").allowed is True

    # Global namespace accessible by all agents
    assert PolicyEngine.evaluate_access(test_db, agent_b, shared_ns, "read").allowed is True