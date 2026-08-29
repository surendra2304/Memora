"""
Agent Registration & Identity Endpoints
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from core.identity.service import IdentityService
from core.memory.schemas import AgentCreate, AgentRead

router = APIRouter(prefix="/agents", tags=["Agents"])

@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def register_agent(agent_in: AgentCreate, db: Session = Depends(get_db)):
    agent = IdentityService.register_agent(db, name=agent_in.name, description=agent_in.description)
    return agent

@router.get("", response_model=List[AgentRead])
def list_agents(db: Session = Depends(get_db)):
    return IdentityService.list_agents(db)

@router.get("/{name}", response_model=AgentRead)
def get_agent(name: str, db: Session = Depends(get_db)):
    agent = IdentityService.get_agent_by_name(db, name=name)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")
    return agent