"""
Seed FRIDAY Universe Database
Initializes the exact 9 canonical agents of the FRIDAY Universe, eliminates
legacy artifacts (ai_universe, nexus, etc.), and seeds realistic, production-grade
memories across all agents.
"""
import os
import sys
import uuid
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from storage.relational.session import SessionLocal, init_db
from storage.relational.models import (
    Agent, Namespace, MemoryRecord, MemoryType, LifecycleState, NamespaceType
)

FRIDAY_AGENTS = [
    {
        "name": "friday",
        "role": "supervisor",
        "description": "Central desktop OS and master ecosystem orchestrator",
        "bounded_scope": None,
        "memories": [
            {
                "type": MemoryType.PREFERENCE,
                "content": "Master Executive User Profile: Dark-mode glassmorphic UI, maximum latency budget of 200ms for desktop notifications, concise bulleted briefings with action items, autonomous execution confirmation threshold set to High.",
                "importance": 0.98,
                "confidence": 1.0,
                "entities": ["UIPreferences", "LatencyBudget", "ExecutiveBriefing"]
            },
            {
                "type": MemoryType.PROCEDURAL,
                "content": "Master Agent Task Orchestration Protocol: Upon incoming user goal, FRIDAY decomposes intent into DAG execution trees, delegates engineering to FORGE, security validation to SENTINEL, external research to INTELX, and aggregates real-time telemetry via CORTEX.",
                "importance": 0.95,
                "confidence": 1.0,
                "entities": ["DAGOrchestration", "TaskDecomposition", "EcosystemCoordination"]
            },
            {
                "type": MemoryType.DECISION,
                "content": "Supervisor Orchestration Topology: Implemented decentralized peer execution with centralized supervisor fallback. All inter-agent capability requests require signed access tokens validated by the Memora 5D policy engine.",
                "importance": 0.92,
                "confidence": 1.0,
                "entities": ["SecurityTopology", "PolicyEngine", "AccessControl"]
            },
            {
                "type": MemoryType.EXPERIENCE,
                "content": "Incident Post-Mortem: Agent task deadlock avoided during concurrent pipeline compilation by introducing strict timeout bounds (30s), non-blocking heartbeat polling, and graceful task degradation.",
                "importance": 0.90,
                "confidence": 0.99,
                "entities": ["DeadlockMitigation", "TimeoutBounds", "HeartbeatMonitoring"]
            },
            {
                "type": MemoryType.PROJECT,
                "content": "FRIDAY Universe v2.0 Architecture Rollout: Finalized cross-agent memory synchronization, Turso cloud database replica, real-time executive dashboard, and multi-tenant isolation protocols.",
                "importance": 0.94,
                "confidence": 1.0,
                "entities": ["FRIDAYUniverse", "MemoraV2", "ExecutiveDashboard"]
            }
        ]
    },
    {
        "name": "forge",
        "role": "worker",
        "description": "Autonomous software engineering, full-stack architecture and code synthesis",
        "bounded_scope": None,
        "memories": [
            {
                "type": MemoryType.EXPERIENCE,
                "content": "[Failure Mode Warning]: In task 'deployment', failure occurred: CrashLoopBackOff: missing database migration on auth_tokens table before pod rollout. [Best Practice]: Mandatory operational guideline: Always execute required pre-flight validations, schema migrations, and health checks before initiating execution.",
                "importance": 0.99,
                "confidence": 0.99,
                "entities": ["CrashLoopBackOff", "DatabaseMigration", "PreflightChecks"]
            },
            {
                "type": MemoryType.DECISION,
                "content": "Architectural Decision: Adopt asynchronous PostgreSQL connection pool with max 20 connections and async SQLAlchemy 2.0 ORM for high-throughput write pipeline, reducing database latency by 45% under load.",
                "importance": 0.92,
                "confidence": 1.0,
                "entities": ["AsyncPostgreSQL", "ConnectionPool", "SQLAlchemy2"]
            },
            {
                "type": MemoryType.PROCEDURAL,
                "content": "Continuous Integration and Automated Delivery Runbook: Execute ruff linting, mypy type verification, full pytest suite (100% pass mandate), verify diary constraints, and build minimal multi-stage Docker artifacts.",
                "importance": 0.90,
                "confidence": 1.0,
                "entities": ["CIRunbook", "Ruff", "Mypy", "Pytest", "Docker"]
            },
            {
                "type": MemoryType.SEMANTIC,
                "content": "Code Synthesis Best Practices: Enforce strict single-responsibility principles, parameterized SQL statements, zero hardcoded credentials, and auto-generated OpenAPI schemas.",
                "importance": 0.88,
                "confidence": 1.0,
                "entities": ["CodeSynthesis", "ParameterizedQueries", "OpenAPISchema"]
            }
        ]
    },
    {
        "name": "sentinel",
        "role": "worker",
        "description": "Cybersecurity shield, threat detection, vulnerability remediation and zero-trust policy defense",
        "bounded_scope": None,
        "memories": [
            {
                "type": MemoryType.EXPERIENCE,
                "content": "Critical Security Incident Mitigation: Detected and patched unauthenticated SQL injection attack surface in user authentication handler by enforcing parameterized queries and Argon2id password hashing across the ecosystem.",
                "importance": 0.98,
                "confidence": 1.0,
                "entities": ["SQLInjection", "Argon2id", "AuthHardening"]
            },
            {
                "type": MemoryType.PROCEDURAL,
                "content": "Zero-Trust Security Audit Protocol: Execute scheduled dependency vulnerability scans with pip-audit, enforce 5D policy matrix default-deny, and verify cryptographic SHA-256 signatures on memory writes.",
                "importance": 0.94,
                "confidence": 1.0,
                "entities": ["ZeroTrust", "PipAudit", "SHA256Signatures"]
            },
            {
                "type": MemoryType.DECISION,
                "content": "Policy Enforcement Decision: All cross-agent memory access without explicit AccessGrant must be blocked with HTTP 403 Forbidden and logged as high-severity security audit events.",
                "importance": 0.95,
                "confidence": 1.0,
                "entities": ["PolicyEnforcement", "DefaultDeny", "SecurityAudit"]
            },
            {
                "type": MemoryType.SEMANTIC,
                "content": "Ecosystem Cryptographic Baseline: Mandate TLS 1.3 for all inter-service communication, ephemeral JWT tokens with 15-minute expirations, and AES-256-GCM encryption for persistent state at rest.",
                "importance": 0.92,
                "confidence": 1.0,
                "entities": ["TLS13", "JWT", "AES256GCM", "DataAtRest"]
            }
        ]
    },
    {
        "name": "inference",
        "role": "worker",
        "description": "Multi-model AI inference gateway, dynamic LLM routing and token budget optimization",
        "bounded_scope": None,
        "memories": [
            {
                "type": MemoryType.DECISION,
                "content": "Dynamic Model Routing Matrix: Route high-reasoning tasks and code synthesis to Claude 3.5 Sonnet / GPT-4o, fast structured extraction to Gemini 1.5 Flash, and latency-critical local queries to quantized Llama 3 models.",
                "importance": 0.94,
                "confidence": 1.0,
                "entities": ["ModelRouting", "Claude35", "GPT4o", "GeminiFlash", "Llama3"]
            },
            {
                "type": MemoryType.PROCEDURAL,
                "content": "KV-Cache Optimization and Context Window Management: Monitor token consumption and invoke Memora Hierarchical Summarizer when context depth exceeds 8,000 tokens to prevent model context saturation.",
                "importance": 0.90,
                "confidence": 1.0,
                "entities": ["KVCache", "TokenBudgeting", "ContextSummarization"]
            },
            {
                "type": MemoryType.EXPERIENCE,
                "content": "Inference Failover Incident: Upstream rate limit encountered on primary cloud provider during peak workload; automatic circuit breaker redirected traffic to secondary endpoint with zero dropped requests.",
                "importance": 0.91,
                "confidence": 0.99,
                "entities": ["RateLimitHandling", "CircuitBreaker", "HighAvailability"]
            },
            {
                "type": MemoryType.SEMANTIC,
                "content": "Inference Hyperparameter Baseline: Temperature 0.2 for deterministic code generation, 0.7 for exploratory research, top_p 0.95, frequency penalty 0.1.",
                "importance": 0.85,
                "confidence": 1.0,
                "entities": ["Hyperparameters", "TemperatureControl", "SamplingStrategy"]
            }
        ]
    },
    {
        "name": "cortex",
        "role": "worker",
        "description": "Autonomous web operations, real-time telemetry intelligence and browser automation",
        "bounded_scope": None,
        "memories": [
            {
                "type": MemoryType.PROCEDURAL,
                "content": "Real-Time Telemetry Gathering Runbook: Ingest system health metrics (p95 latency, RAM utilization, worker thread queue backlog) every 60 seconds; raise alert if memory exceeds 85% threshold.",
                "importance": 0.92,
                "confidence": 1.0,
                "entities": ["TelemetryRunbook", "P95Latency", "ResourceMonitoring"]
            },
            {
                "type": MemoryType.DECISION,
                "content": "Headless Browser Driver Architecture: Deploy Playwright Chromium with anti-detection fingerprint shielding for deep-web scraping, synthetic user journeys, and live UI rendering health checks.",
                "importance": 0.90,
                "confidence": 1.0,
                "entities": ["PlaywrightChromium", "BrowserAutomation", "FingerprintShielding"]
            },
            {
                "type": MemoryType.EXPERIENCE,
                "content": "Browser Session Optimization: Mitigated Chromium memory bloat during continuous 24h background scraping by enforcing page context recycling every 100 navigation cycles.",
                "importance": 0.88,
                "confidence": 0.98,
                "entities": ["MemoryLeakMitigation", "ContextRecycling", "WebScraping"]
            },
            {
                "type": MemoryType.PROJECT,
                "content": "FRIDAY Universe Telemetry Dashboard: Real-time telemetry streaming and agent status visualization operational on Render with WebSocket notifications.",
                "importance": 0.89,
                "confidence": 1.0,
                "entities": ["TelemetryDashboard", "WebSocketStreaming", "RenderDeployment"]
            }
        ]
    },
    {
        "name": "intelx",
        "role": "worker",
        "description": "Deep research intelligence, autonomous investigation, evidence extraction and synthesis",
        "bounded_scope": None,
        "memories": [
            {
                "type": MemoryType.PROCEDURAL,
                "content": "Multi-Source Evidence Investigation Protocol: Triangulate academic papers, technical documentation, and market intelligence using triplet fact-checking and automated citation graph building.",
                "importance": 0.93,
                "confidence": 1.0,
                "entities": ["FactChecking", "CitationGraph", "EvidenceTriangulation"]
            },
            {
                "type": MemoryType.DECISION,
                "content": "Hybrid Knowledge Retrieval Architecture: Combine BM25 lexical keyword matching with dense vector embeddings using Reciprocal Rank Fusion (RRF) to eliminate hallucinated retrieval results.",
                "importance": 0.95,
                "confidence": 1.0,
                "entities": ["HybridRetrieval", "BM25", "VectorEmbeddings", "RRF"]
            },
            {
                "type": MemoryType.EXPERIENCE,
                "content": "Conflicting Data Reconciliation Case Study: Successfully harmonized contradicting analyst forecasts across 4 competing sources by calculating historical variance weights and prioritizing primary source documentation.",
                "importance": 0.89,
                "confidence": 0.98,
                "entities": ["DataReconciliation", "SourceEvaluation", "VarianceWeighting"]
            },
            {
                "type": MemoryType.SEMANTIC,
                "content": "Evidence Quality Classification Taxonomy: Tier-1 (peer-reviewed / verified official docs), Tier-2 (reputable industry analytics), Tier-3 (speculative or unverified social sources).",
                "importance": 0.87,
                "confidence": 1.0,
                "entities": ["EvidenceTaxonomy", "SourceVerification", "QualityTiers"]
            }
        ]
    },
    {
        "name": "futuris",
        "role": "worker",
        "description": "Probabilistic forecasting, macroeconomic and asset regime modeling, scenario simulation",
        "bounded_scope": None,
        "memories": [
            {
                "type": MemoryType.DECISION,
                "content": "Probabilistic Forecasting Architecture: Deploy regime-switching Gaussian mixture models combined with 10,000 Monte Carlo iteration simulations for macroeconomic and asset price trajectory projections.",
                "importance": 0.95,
                "confidence": 1.0,
                "entities": ["RegimeSwitching", "GaussianMixtures", "MonteCarlo"]
            },
            {
                "type": MemoryType.PROCEDURAL,
                "content": "Scenario Stress-Testing Protocol: When market volatility index exceeds 2.5 standard deviations, generate stress-test counterfactual scenarios and dispatch risk matrices to STRATEX for hedging adjustment.",
                "importance": 0.93,
                "confidence": 1.0,
                "entities": ["StressTesting", "VolatilityAlerts", "CounterfactualScenarios"]
            },
            {
                "type": MemoryType.EXPERIENCE,
                "content": "Regime Transition Early Detection: Correctly predicted transition from low-volatility expansion to liquidity contraction 48 hours in advance with 87% statistical confidence interval.",
                "importance": 0.94,
                "confidence": 0.99,
                "entities": ["RegimeTransition", "LiquidityContraction", "PredictiveAccuracy"]
            },
            {
                "type": MemoryType.SEMANTIC,
                "content": "Market Regime Taxonomy: Regime 1 (Calm Bull), Regime 2 (High-Volatility Expansion), Regime 3 (Liquidity Squeeze), Regime 4 (Crisis Contraction).",
                "importance": 0.88,
                "confidence": 1.0,
                "entities": ["MarketRegimes", "ClassificationTaxonomy", "VolatilityMetrics"]
            }
        ]
    },
    {
        "name": "stratex",
        "role": "worker",
        "description": "24/7 algorithmic trading, multi-asset order execution engine and risk guardrails",
        "bounded_scope": None,
        "memories": [
            {
                "type": MemoryType.DECISION,
                "content": "Hard Risk Management Guardrails: Maximum allowable drawdown capped at 2.0% per individual asset; gross portfolio exposure restricted to 300%; automatic circuit breaker halt on slippage exceeding 15 bps.",
                "importance": 0.98,
                "confidence": 1.0,
                "entities": ["RiskGuardrails", "MaxDrawdown", "SlippageProtection"]
            },
            {
                "type": MemoryType.PROCEDURAL,
                "content": "Smart Order Routing (SOR) Playbook: Slice large institutional orders into Time-Weighted Average Price (TWAP) and Volume-Weighted Average Price (VWAP) sub-orders across execution venues to minimize market impact.",
                "importance": 0.94,
                "confidence": 1.0,
                "entities": ["SmartOrderRouting", "TWAP", "VWAP", "MarketImpact"]
            },
            {
                "type": MemoryType.EXPERIENCE,
                "content": "Flash Liquidity Defense: Execution engine detected toxic order flow during illiquid market gap and initiated microsecond cancellations, preventing $45,000 in adverse execution slippage.",
                "importance": 0.95,
                "confidence": 0.99,
                "entities": ["ToxicOrderFlow", "MicrosecondExecution", "AdverseSelection"]
            },
            {
                "type": MemoryType.SEMANTIC,
                "content": "Low-Latency FIX Execution Specifications: Sub-millisecond direct market access (DMA) protocol integration, co-located matching engines, and real-time PnL tracking.",
                "importance": 0.90,
                "confidence": 1.0,
                "entities": ["FIXProtocol", "DirectMarketAccess", "LowLatency"]
            }
        ]
    },
    {
        "name": "memora",
        "role": "worker",
        "description": "Unified persistent memory fabric, dynamic context distillation and knowledge graph subsystem",
        "bounded_scope": None,
        "memories": [
            {
                "type": MemoryType.DECISION,
                "content": "Dual-Tier Persistent Memory Architecture: Relational SQLite/Turso engine provides ACID guarantees and tamper-proof audit trails, paired with Qdrant vector engine for semantic recall.",
                "importance": 0.96,
                "confidence": 1.0,
                "entities": ["DualTierStorage", "TursoCloud", "QdrantVector", "ACID"]
            },
            {
                "type": MemoryType.PROCEDURAL,
                "content": "Memory Lifecycle Decay and Consolidation Runbook: Apply exponential Ebbinghaus memory decay to episodic events; consolidate recurring memories into permanent semantic nodes after 3 verifications.",
                "importance": 0.93,
                "confidence": 1.0,
                "entities": ["EbbinghausDecay", "MemoryConsolidation", "KnowledgeDistillation"]
            },
            {
                "type": MemoryType.EXPERIENCE,
                "content": "Semantic Deduplication Optimization: Eliminated redundant memory writes by enforcing SHA-256 content hashing and cosine similarity distance checks (>0.92) before database insertion.",
                "importance": 0.92,
                "confidence": 0.99,
                "entities": ["DeduplicationPipeline", "SHA256Hashing", "CosineDistance"]
            },
            {
                "type": MemoryType.SEMANTIC,
                "content": "5-Dimensional Policy Engine Matrix: Enforces strict multi-tenancy, actor verification, private namespace ACLs, TTL expiration, and tamper-proof audit trails across all ecosystem operations.",
                "importance": 0.95,
                "confidence": 1.0,
                "entities": ["5DPolicyMatrix", "MultiTenancy", "AccessControlList", "AuditImmutability"]
            }
        ]
    }
]

def seed_database():
    init_db()
    db: Session = SessionLocal()
    try:
        print("[*] Purging legacy non-FRIDAY agents and orphaned records...")
        legacy_names = ["ai_universe", "nexus", "friday:worker-1", "mt5"]
        for leg in legacy_names:
            agent = db.query(Agent).filter(Agent.name == leg).first()
            if agent:
                db.query(MemoryRecord).filter(MemoryRecord.owner_id == agent.id).delete()
                db.query(Namespace).filter(Namespace.agent_id == agent.id).delete()
                db.delete(agent)
                print(f"    [-] Removed legacy agent: {leg}")
        db.commit()

        # Clear existing memories to ensure pristine, rich seed
        db.query(MemoryRecord).delete()
        db.commit()

        print("[*] Registering and Updating 9 FRIDAY Universe Agents...")
        agent_instances = {}
        for a_data in FRIDAY_AGENTS:
            agent = db.query(Agent).filter(Agent.name == a_data["name"]).first()
            if not agent:
                agent = Agent(
                    id=str(uuid.uuid4()),
                    name=a_data["name"],
                    role=a_data["role"],
                    description=a_data["description"],
                    bounded_scope=a_data["bounded_scope"],
                    tenant_id="default"
                )
                db.add(agent)
                db.flush()
                print(f"    [+] Created agent: {a_data['name']} ({a_data['role']})")
            else:
                agent.role = a_data["role"]
                agent.description = a_data["description"]
                agent.bounded_scope = a_data["bounded_scope"]
                db.flush()
                print(f"    [*] Updated agent: {a_data['name']} ({a_data['role']})")

            agent_instances[a_data["name"]] = agent

            # Ensure private namespace
            ns_path = f"memora://{a_data['name']}/private"
            ns = db.query(Namespace).filter(Namespace.path == ns_path).first()
            if not ns:
                ns = Namespace(
                    id=str(uuid.uuid4()),
                    path=ns_path,
                    type=NamespaceType.PRIVATE.value,
                    agent_id=agent.id,
                    tenant_id="default"
                )
                db.add(ns)
                db.flush()

        db.commit()

        print("[*] Seeding rich operational memories for all 9 agents...")
        mem_count = 0
        now = datetime.datetime.now(datetime.timezone.utc)

        for a_data in FRIDAY_AGENTS:
            agent = agent_instances[a_data["name"]]
            ns_path = f"memora://{a_data['name']}/private"
            ns = db.query(Namespace).filter(Namespace.path == ns_path).first()

            for i, mem in enumerate(a_data["memories"]):
                rec = MemoryRecord(
                    id=str(uuid.uuid4()),
                    namespace_id=ns.id,
                    owner_id=agent.id,
                    memory_type=mem["type"].value,
                    content_text=mem["content"],
                    source=f"{a_data['name']}_subsystem",
                    confidence=mem["confidence"],
                    importance=mem["importance"],
                    lifecycle_state=LifecycleState.ACTIVE.value,
                    created_at=now - datetime.timedelta(hours=(len(a_data['memories']) - i) * 3),
                    entities=mem["entities"],
                    tenant_id="default"
                )
                db.add(rec)
                mem_count += 1

        db.commit()

        print(f"\n[SUCCESS] Successfully seeded FRIDAY Universe!")
        print(f"  Total Agents: {db.query(Agent).count()}")
        print(f"  Total Memories: {db.query(MemoryRecord).count()}")
        for a in db.query(Agent).all():
            m_count = db.query(MemoryRecord).filter(MemoryRecord.owner_id == a.id).count()
            print(f"  - {a.name.upper():<12} ({a.role:<10}): {m_count} memories | {a.description}")

    except Exception as e:
        db.rollback()
        print(f"[!] Error seeding database: {e}")
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
