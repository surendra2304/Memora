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

class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
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

class AccessGrantCreate(BaseModel):
    agent_name: str
    namespace_path: str
    actions: List[str] = Field(default_factory=lambda: ["read"])
    purpose: Optional[str] = None
    ttl_hours: Optional[int] = None

class AccessGrantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    namespace_id: str
    actions: List[str]
    purpose: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

class NamespaceCreate(BaseModel):
    path: str = Field(..., min_length=3, max_length=256, description="URI format path (e.g. 'memora://forge/projects/alpha')")
    type: NamespaceType = Field(default=NamespaceType.AGENT_PRIVATE)
    agent_name: Optional[str] = None

class NamespaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    path: str
    type: NamespaceType
    agent_id: Optional[str] = None
    created_at: datetime

class MemoryRecordCreate(BaseModel):
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

class MemoryRecordUpdate(BaseModel):
    content_text: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    lifecycle_state: Optional[LifecycleState] = None
    provenance: Optional[Dict[str, Any]] = None

class MemoryRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
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

class MemoryQuery(BaseModel):
    query_text: Optional[str] = None
    namespace_path: Optional[str] = None
    owner_name: Optional[str] = None
    memory_types: Optional[List[MemoryType]] = None
    lifecycle_states: Optional[List[LifecycleState]] = None
    include_superseded: bool = False
    include_archived: bool = False
    include_deleted: bool = False
    min_confidence: Optional[float] = None
    min_importance: Optional[float] = None
    limit: int = 50
    offset: int = 0

class MemoryTransitionRequest(BaseModel):
    target_state: LifecycleState
    superseded_by_id: Optional[str] = None
    purpose: Optional[str] = None

class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: Optional[str] = None
    memory_id: Optional[str] = None
    action: str
    timestamp: datetime
    details: Optional[Dict[str, Any]] = None