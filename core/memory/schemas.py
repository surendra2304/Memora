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
    tenant_id: str = Field(default="default")
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    namespace_id: Optional[str] = None
    namespace_path: Optional[str] = None
    actions: List[str] = Field(default_factory=lambda: ["read"])
    purpose: Optional[str] = None
    expires_at: Optional[datetime] = None
    ttl_hours: Optional[int] = None

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
    tenant_id: str = Field(default="default")
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None

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
    tenant_id: str = Field(default="default")
    user_id: str = Field(default="default_user", description="Identity scope: User identifier")
    agent_id: Optional[str] = Field(default=None, description="Identity scope: Agent identifier")
    workspace_id: str = Field(default="default_workspace", description="Identity scope: Workspace boundary")
    device_id: str = Field(default="default_device", description="Identity scope: Device ID")
    task_id: Optional[str] = Field(default=None, description="Identity scope: Task context ID")
    idempotency_key: Optional[str] = Field(default=None, description="Idempotency key for deduplicated writes")
    content_text: str = Field(..., min_length=1)
    memory_type: MemoryType = Field(default=MemoryType.EPISODIC)
    namespace_path: Optional[str] = None
    namespace_id: Optional[str] = None
    owner_name: Optional[str] = None
    owner_id: Optional[str] = None
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
    content_text: Optional[str] = None
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
    tenant_id: str = "default"
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    workspace_id: Optional[str] = None
    task_id: Optional[str] = None
    trust_level: Optional[str] = None
    time_from: Optional[datetime] = None
    time_to: Optional[datetime] = None
    query_text: Optional[str] = None
    namespace_path: Optional[str] = None
    owner_name: Optional[str] = None
    memory_types: Optional[List[MemoryType]] = None
    lifecycle_states: Optional[List[LifecycleState]] = None
    include_superseded: bool = False
    include_archived: bool = False
    include_deleted: bool = False
    include_expired: bool = False
    min_confidence: Optional[float] = None
    min_importance: Optional[float] = None
    limit: int = 50
    offset: int = 0

class MemoryTransitionRequest(BaseModel):
    target_state: LifecycleState
    superseded_by_id: Optional[str] = None
    purpose: Optional[str] = None

class MemoryPromoteRequest(BaseModel):
    """Explicit promotion request from episodic or working memory into semantic memory."""
    promoted_by: str = Field(default="friday", description="Agent or user promoting this memory")
    verification_evidence: List[str] = Field(..., min_length=1, description="Evidence references or corroborating sources")
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