"""
Memory Ingestion, Query, Lifecycle, and Retrieval Endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from storage.relational.models import MemoryType, LifecycleState
from core.memory.service import (
    MemoryService,
    MemoryNotFoundError,
    PermissionDeniedError
)
from core.memory.schemas import (
    MemoryRecordCreate,
    MemoryRecordRead,
    MemoryRecordUpdate,
    MemoryQuery,
    MemoryTransitionRequest
)
from apps.api.dependencies import get_actor_header

router = APIRouter(prefix="/memories", tags=["Memories"])

@router.post("", response_model=MemoryRecordRead, status_code=status.HTTP_201_CREATED)
def ingest_memory(
    memory_in: MemoryRecordCreate,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        record = MemoryService.create_memory(db, memory_in, actor_name=actor_name)
        return record
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/{memory_id}", response_model=MemoryRecordRead)
def get_memory(
    memory_id: str,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        return MemoryService.get_memory_by_id(db, memory_id=memory_id, actor_name=actor_name)
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.post("/query", response_model=List[MemoryRecordRead])
def query_memories(
    query: MemoryQuery,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    return MemoryService.query_memories(db, query=query, actor_name=actor_name)

@router.post("/{memory_id}/transition", response_model=MemoryRecordRead)
def transition_memory_lifecycle(
    memory_id: str,
    req: MemoryTransitionRequest,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        return MemoryService.transition_memory_state(
            db,
            memory_id=memory_id,
            target_state=req.target_state,
            actor_name=actor_name,
            superseded_by_id=req.superseded_by_id
        )
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))