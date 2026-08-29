"""
Pydantic Domain Schemas for Memora API
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from storage.relational.models import NamespaceType, MemoryType, LifecycleState

# Agent Schemas
class AgentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    role: str = "worker"

class AgentCreate(AgentBase):
    pass

class SubAgentCreate(BaseModel):
    parent_agent_name: str
    subagent_name: str
    bounded_scope: str = Field(..., description="Namespace URI scope the sub-agent is restricted to")
    description: Optional[str] = None

class AgentRead(AgentBase):
    id: str
    parent_agent_id: Optional[str] = None
    bounded_scope: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# Access Grant Schemas
class AccessGrantBase(BaseModel):
    agent_id: str
    namespace_id: str
    actions: List[str] = Field(default_factory=lambda: ["read", "query"])
    purpose: Optional[str] = None
    expires_at: Optional[datetime] = None

class AccessGrantCreate(AccessGrantBase):
    pass

class AccessGrantRead(AccessGrantBase):
    id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# Namespace Schemas
class NamespaceBase(BaseModel):
    path: str = Field(..., min_length=1, max_length=256)
    type: NamespaceType = NamespaceType.AGENT_PRIVATE
    agent_id: Optional[str] = None

class NamespaceCreate(NamespaceBase):
    pass

class NamespaceRead(NamespaceBase):
    id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# Memory Record Schemas
class MemoryRecordBase(BaseModel):
    content_text: str = Field(..., min_length=1)
    memory_type: MemoryType = MemoryType.EPISODIC
    source: str = "unknown"
    provenance: Optional[Dict[str, Any]] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)

class MemoryRecordCreate(MemoryRecordBase):
    namespace_path: Optional[str] = None
    namespace_id: Optional[str] = None
    owner_name: Optional[str] = None
    owner_id: Optional[str] = None
    lifecycle_state: LifecycleState = LifecycleState.CANDIDATE

class MemoryRecordUpdate(BaseModel):
    content_text: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    lifecycle_state: Optional[LifecycleState] = None
    superseded_by_id: Optional[str] = None

class MemoryTransitionRequest(BaseModel):
    target_state: LifecycleState
    superseded_by_id: Optional[str] = None
    purpose: Optional[str] = None

class MemoryRecordRead(MemoryRecordBase):
    id: str
    namespace_id: str
    owner_id: str
    lifecycle_state: LifecycleState
    created_at: datetime
    last_verified_at: Optional[datetime] = None
    superseded_by_id: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class MemoryQuery(BaseModel):
    query_text: Optional[str] = None
    namespace_path: Optional[str] = None
    owner_name: Optional[str] = None
    memory_types: Optional[List[MemoryType]] = None
    lifecycle_states: Optional[List[LifecycleState]] = None
    min_confidence: Optional[float] = 0.0
    min_importance: Optional[float] = 0.0
    limit: int = 50
    offset: int = 0

# Audit Log Schemas
class AuditLogRead(BaseModel):
    id: str
    actor_id: Optional[str] = None
    memory_id: Optional[str] = None
    action: str
    timestamp: datetime
    details: Optional[Dict[str, Any]] = None
    model_config = ConfigDict(from_attributes=True)