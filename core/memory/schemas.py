"""
Pydantic Schemas for Memora API & Memory Service
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from storage.relational.models import MemoryType, LifecycleState, NamespaceType

class AgentCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=128, description="Unique agent handle (e.g. 'friday', 'forge')")
    description: Optional[str] = None
    role: str = Field(default="worker", description="Role/authority level")
    tenant_id: str = Field(default="default", description="Tenant identifier")

class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str = "default"
    name: str
    description: Optional[str] = None
    role: str
    parent_agent_id: Optional[str] = None
    bounded_scope: Optional[str] = None
    created_at: datetime

class SubAgentCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    description: Optional[str] = None
    role: str = Field(default="worker")
    bounded_scope: str = Field(..., description="Target bounded namespace subtree (e.g. 'memora://forge/projects/app-17')")
    tenant_id: str = Field(default="default")

class AccessGrantCreate(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    agent_id: Optional[str] = Field(default=None, max_length=64)
    agent_name: Optional[str] = Field(default=None, min_length=2, max_length=128)
    namespace_id: Optional[str] = Field(default=None, max_length=64)
    namespace_path: Optional[str] = Field(default=None, min_length=3, max_length=1024)
    actions: List[str] = Field(default_factory=lambda: ["read"], min_length=1, max_length=3)
    purpose: Optional[str] = Field(default=None, max_length=512)
    expires_at: Optional[datetime] = None
    ttl_hours: Optional[int] = Field(default=None, ge=1, le=8760)

class AccessGrantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str = "default"
    agent_id: str
    namespace_id: str
    actions: List[str]
    purpose: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

class NamespaceCreate(BaseModel):
    path: str = Field(..., min_length=3, max_length=256, description="URI format path (e.g. 'memora://forge/projects/alpha')")
    type: NamespaceType = Field(default=NamespaceType.AGENT_PRIVATE)
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    agent_id: Optional[str] = Field(default=None, max_length=64)
    agent_name: Optional[str] = Field(default=None, min_length=2, max_length=128)

class NamespaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str = "default"
    path: str
    type: NamespaceType
    agent_id: Optional[str] = None
    created_at: datetime

class ProvenanceMetadata(BaseModel):
    """Structured provenance required on external or agent-generated knowledge."""
    source: str = Field(default="unknown")
    source_type: str = Field(default="agent_generated")  # "agent_generated", "user_input", "tool_output", "web_scrape", "ocr", "model_output", "verified_fact"
    trust_level: str = Field(default="untrusted")  # "untrusted", "candidate", "verified", "operator_confirmed"
    evidence_refs: List[str] = Field(default_factory=list)
    created_by: str = Field(default="system")
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

class MemoryRecordCreate(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    user_id: str = Field(default="default_user", max_length=128, description="Identity scope: User identifier")
    agent_id: Optional[str] = Field(default=None, max_length=128, description="Identity scope: Agent identifier")
    workspace_id: str = Field(default="default_workspace", max_length=128, description="Identity scope: Workspace boundary")
    device_id: str = Field(default="default_device", max_length=128, description="Identity scope: Device ID")
    task_id: Optional[str] = Field(default=None, max_length=128, description="Identity scope: Task context ID")
    idempotency_key: Optional[str] = Field(default=None, max_length=128, description="Idempotency key for deduplicated writes")
    content_text: str = Field(..., min_length=1, max_length=100_000)
    memory_type: MemoryType = Field(default=MemoryType.EPISODIC)
    namespace_path: Optional[str] = Field(default=None, max_length=1024)
    namespace_id: Optional[str] = Field(default=None, max_length=64)
    owner_name: Optional[str] = Field(default=None, max_length=128)
    owner_id: Optional[str] = Field(default=None, max_length=64)
    source: str = Field(default="unknown")
    provenance: Optional[Dict[str, Any]] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    lifecycle_state: Optional[LifecycleState] = Field(default=LifecycleState.CANDIDATE)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    entities: List[str] = Field(default_factory=list)

class MemoryRecordUpdate(BaseModel):
    content_text: Optional[str] = Field(default=None, max_length=100_000)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    lifecycle_state: Optional[LifecycleState] = None
    provenance: Optional[Dict[str, Any]] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    entities: Optional[List[str]] = None

class MemoryRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str = "default"
    user_id: str = "default_user"
    agent_id: str = "friday"
    workspace_id: str = "default_workspace"
    device_id: str = "default_device"
    task_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    namespace_id: str
    owner_id: str
    memory_type: MemoryType
    content_text: str
    source: str
    provenance: Optional[Dict[str, Any]] = None
    confidence: float
    importance: float
    lifecycle_state: LifecycleState
    created_at: datetime
    last_verified_at: Optional[datetime] = None
    superseded_by_id: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    entities: Optional[List[str]] = None

class MemoryQuery(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    user_id: Optional[str] = Field(default=None, max_length=128)
    agent_id: Optional[str] = Field(default=None, max_length=128)
    workspace_id: Optional[str] = Field(default=None, max_length=128)
    task_id: Optional[str] = Field(default=None, max_length=128)
    trust_level: Optional[str] = Field(default=None, max_length=32)
    time_from: Optional[datetime] = None
    time_to: Optional[datetime] = None
    query_text: Optional[str] = None
    namespace_path: Optional[str] = Field(default=None, max_length=1024)
    owner_name: Optional[str] = Field(default=None, max_length=128)
    memory_types: Optional[List[MemoryType]] = Field(default=None, max_length=12)
    lifecycle_states: Optional[List[LifecycleState]] = Field(default=None, max_length=6)
    include_superseded: bool = False
    include_archived: bool = False
    include_deleted: bool = False
    include_expired: bool = False
    min_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    min_importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=1_000_000)

class MemoryTransitionRequest(BaseModel):
    target_state: LifecycleState
    superseded_by_id: Optional[str] = Field(default=None, max_length=64)
    purpose: Optional[str] = Field(default=None, max_length=512)

class MemoryPromoteRequest(BaseModel):
    """Evidence-backed promotion; the actor always comes from authentication."""
    verification_evidence: List[str] = Field(
        ..., min_length=1, max_length=32,
        description="Evidence references or corroborating sources",
    )
    target_confidence: float = Field(default=0.95, ge=0.85, le=1.0, description="Calibrated confidence score")
    purpose: Optional[str] = Field(default="Verified empirical promotion into semantic tier")

class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str = "default"
    actor_id: Optional[str] = None
    memory_id: Optional[str] = None
    action: str
    timestamp: datetime
    details: Optional[Dict[str, Any]] = None