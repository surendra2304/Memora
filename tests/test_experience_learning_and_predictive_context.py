"""
E2E Integration Tests for Experience Learning and Predictive Context Pre-Fetching
Proves that if an agent (e.g. FORGE) previously failed at a deployment step,
that past failure experience is automatically synthesized, stored, and predictive
context pre-fetching injects the past lesson at the top of the next deployment context bundle.
"""
import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from apps.api.main import app
from storage.relational.models import MemoryRecord, MemoryType, LifecycleState
from core.identity.service import IdentityService
from core.memory.context.builder import ContextBuilderService
from core.memory.experience_service import ExperienceLearnerService, TaskOutcome

@pytest.fixture
def api_client():
    return TestClient(app)

def test_learn_experience_api_endpoint(api_client, test_db):
    """
    Test POST /v1/memories/learn-experience endpoint successfully extracts
    operational guidelines from a batch of failed/successful task executions.
    """
    IdentityService.register_agent(test_db, "forge", role="worker")

    payload = {
        "agent_id": "forge",
        "namespace_path": "memora://forge/private",
        "outcomes": [
            {
                "task_name": "kubernetes-staging-rollout",
                "status": "failure",
                "domain": "deployment",
                "error_log": "CrashLoopBackOff: database schema migration was omitted before pod startup, causing missing column error.",
                "actions_taken": "helm upgrade --install auth-service ./chart",
                "context": "Staging Kubernetes Cluster"
            },
            {
                "task_name": "database-migration-check",
                "status": "success",
                "domain": "deployment",
                "actions_taken": "alembic upgrade head executed successfully"
            }
        ]
    }

    resp = api_client.post(
        "/v1/memories/learn-experience",
        json=payload,
        headers={"X-Agent-Name": "forge"}
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["memory_type"] == "experience"
    assert data["importance"] >= 0.95
    assert "Failure Mode Warning" in data["content_text"]
    assert "database schema migration was omitted" in data["content_text"]

def test_e2e_predictive_context_injection_for_past_deployment_failure(test_db):
    """
    Real-world Scenario:
    1. FORGE previously failed a deployment step due to omitted database migrations.
    2. Experience learning extracts and stores this lesson as MemoryType.EXPERIENCE.
    3. Several other episodic and semantic memories exist for FORGE (e.g. general notes, UI styling).
    4. When FORGE next requests context for 'Deploy auth-module to production cluster',
       the Context Builder pre-fetches the past failure Experience and places it at the very top of the Context Bundle!
    """
    forge = IdentityService.register_agent(test_db, "forge", role="worker")
    ns_forge = IdentityService.resolve_namespace(test_db, "memora://forge/private", owner_agent_id=forge.id)

    # 1. Ingest background episodic/semantic memories
    rec_episodic = MemoryRecord(
        id="mem-forge-episodic-01",
        namespace_id=ns_forge.id,
        owner_id=forge.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Deploy auth-module button was clicked in the admin UI dashboard.",
        confidence=0.90,
        importance=0.60,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=datetime.now(timezone.utc)
    )
    rec_semantic = MemoryRecord(
        id="mem-forge-semantic-02",
        namespace_id=ns_forge.id,
        owner_id=forge.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="Production cluster nodes are hosted in us-east-1 AWS data center.",
        confidence=0.90,
        importance=0.70,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=datetime.now(timezone.utc)
    )
    test_db.add_all([rec_episodic, rec_semantic])
    test_db.commit()

    # 2. Learn Experience from past deployment failure
    outcomes = [
        TaskOutcome(
            task_name="auth-module-deployment",
            status="failure",
            domain="deployment",
            error_log="Pod crashed with fatal error: table 'auth_tokens' missing column 'revoked_at'. Database migration was not applied before deployment.",
            actions_taken="kubectl apply -f deployment.yaml",
            context="Kubernetes cluster rollout"
        )
    ]
    exp_record = ExperienceLearnerService.learn_experience(
        db=test_db,
        actor_name="forge",
        outcomes=outcomes,
        namespace_path="memora://forge/private"
    )

    assert exp_record.memory_type == MemoryType.EXPERIENCE
    assert exp_record.id is not None

    # 3. FORGE requests context bundle for a new deployment query
    new_deployment_query = "Deploy auth-module to production cluster"
    bundle = ContextBuilderService.build_context_bundle(
        db=test_db,
        agent_id_or_name="forge",
        task_query=new_deployment_query,
        token_budget=4000
    )

    # 4. Verify that the Experience memory is automatically pre-fetched and ranked #1 at the top!
    assert len(bundle.memories) >= 1
    top_memory = bundle.memories[0]
    
    # Assert top memory is the Experience memory warning
    assert top_memory["memory_type"] == "experience", f"Top memory must be the predictive Experience lesson! Got: {top_memory['memory_type']}"
    assert "Failure Mode Warning" in top_memory["content_text"]
    assert "Database migration was not applied" in top_memory["content_text"]