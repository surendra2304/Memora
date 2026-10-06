"""
Agent Registration & Identity Endpoints
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from core.identity.service import IdentityService
from core.memory.schemas import AgentCreate, SubAgentCreate, AgentRead
from apps.api.dependencies import authenticate_agent, get_actor_header, require_admin

# Agent identity is the root of every policy decision, so identity registration
# and enumeration require a mesh credential. Authentication alone is not enough
# for creation: any agent with a valid key could mint a new identity, including
# one with role="supervisor", and then act under it. Creating an identity is
# fabric administration, so it is restricted to the Memora service identity.
# Enumeration stays open to any authenticated agent, because agents need to
# discover each other to collaborate.
router = APIRouter(
    prefix="/agents",
    tags=["Agents"],
    dependencies=[Depends(authenticate_agent)],
)

@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def register_agent(
    agent_in: AgentCreate,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
):
    require_admin(actor_name)
    agent = IdentityService.register_agent(
        db,
        name=agent_in.name,
        description=agent_in.description,
        role=agent_in.role
    )
    return agent

@router.post("/subagents", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def register_subagent(
    subagent_in: SubAgentCreate,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
):
    """Create a sub-agent under the calling agent.

    This endpoint could not work at all. It read `subagent_in.parent_agent_name`
    and `subagent_in.subagent_name`, neither of which exists on SubAgentCreate
    (its fields are name, description, role, bounded_scope, tenant_id), so every
    valid request raised AttributeError and returned HTTP 500. Verified directly:
    a correctly shaped body produced 500 Internal Server Error.

    The parent is now the authenticated caller rather than a request field. That
    fixes the crash and removes the escalation vector at the same time: a
    sub-agent inherits its parent's standing, so letting a peer name friday as
    the parent would have been an escalation. There is no longer a way to ask for
    a parent you are not.
    """
    caller = IdentityService.get_agent_by_name(db, actor_name, tenant_id=subagent_in.tenant_id)
    if not caller:
        # register_subagent would otherwise silently create an agent named after
        # the caller, minting an identity as a side effect of a failed request.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Authenticated agent '{actor_name}' is not registered in Memora.",
        )
    subagent = IdentityService.register_subagent(
        db,
        parent_agent_name=caller.name,
        subagent_name=subagent_in.name,
        bounded_scope=subagent_in.bounded_scope,
        description=subagent_in.description,
        tenant_id=caller.tenant_id,
    )
    return subagent

@router.get("", response_model=List[AgentRead])
def list_agents(db: Session = Depends(get_db)):
    return IdentityService.list_agents(db)

@router.get("/{name}", response_model=AgentRead)
def get_agent(name: str, db: Session = Depends(get_db)):
    agent = IdentityService.get_agent_by_name(db, name=name)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")
    return agent