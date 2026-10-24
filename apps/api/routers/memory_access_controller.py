"""
Memora/apps/api/routers/memory_access_controller.py — Memora Memory Access Controller.

Permission management, access grants, scoping, audit trails, and
role-based access control for memory operations.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class AccessGrant:
    """An access grant for a memory scope."""
    grant_id: str
    subject: str  # agent name, user, or role
    scope: str  # memory namespace or collection
    permission: str = "read"  # read, write, delete, admin
    granted_at: float = field(default_factory=time.time)
    granted_by: str = "system"
    expires_at: float = 0.0


@dataclass
class AccessRequest:
    """An access request."""
    request_id: str
    subject: str
    scope: str
    permission: str
    reason: str = ""
    status: str = "pending"  # pending, approved, denied
    created_at: float = field(default_factory=time.time)
    decided_at: float = 0.0
    decided_by: str = ""


@dataclass
class AccessAuditEntry:
    """An audit entry for memory access."""
    entry_id: str
    subject: str
    scope: str
    operation: str  # read, write, delete
    granted: bool = True
    timestamp: float = field(default_factory=time.time)
    reason: str = ""


class MemoryAccessController:
    """Controller for memory access permissions."""

    def __init__(self) -> None:
        self._grants: dict[str, AccessGrant] = {}
        self._requests: dict[str, AccessRequest] = {}
        self._audit: list[AccessAuditEntry] = []

    def grant_access(self, subject: str, scope: str, permission: str = "read",
                     granted_by: str = "system", ttl_seconds: float = 0.0) -> str:
        """Grant access to a memory scope."""
        grant_id = f"grant_{uuid.uuid4().hex[:12]}"
        grant = AccessGrant(
            grant_id=grant_id, subject=subject, scope=scope,
            permission=permission, granted_by=granted_by,
            expires_at=time.time() + ttl_seconds if ttl_seconds > 0 else 0.0,
        )
        self._grants[grant_id] = grant
        return grant_id

    def revoke_access(self, grant_id: str) -> bool:
        """Revoke an access grant."""
        if grant_id not in self._grants:
            return False
        del self._grants[grant_id]
        return True

    def check_access(self, subject: str, scope: str, permission: str = "read") -> bool:
        """Check if a subject has access to a scope."""
        for grant in self._grants.values():
            if grant.subject == subject and grant.scope == scope and grant.permission == permission:
                if grant.expires_at == 0 or grant.expires_at > time.time():
                    self._audit.append(AccessAuditEntry(
                        entry_id=f"audit_{uuid.uuid4().hex[:8]}",
                        subject=subject, scope=scope, operation=permission, granted=True,
                    ))
                    return True
        self._audit.append(AccessAuditEntry(
            entry_id=f"audit_{uuid.uuid4().hex[:8]}",
            subject=subject, scope=scope, operation=permission, granted=False,
            reason="No matching grant found",
        ))
        return False

    def request_access(self, subject: str, scope: str, permission: str,
                       reason: str = "") -> str:
        """Request access to a memory scope."""
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        request = AccessRequest(
            request_id=request_id, subject=subject, scope=scope,
            permission=permission, reason=reason,
        )
        self._requests[request_id] = request
        return request_id

    def approve_request(self, request_id: str, decided_by: str = "system") -> AccessRequest:
        """Approve an access request."""
        request = self._requests.get(request_id)
        if not request:
            raise HTTPException(status_code=404, detail="Request not found")
        request.status = "approved"
        request.decided_at = time.time()
        request.decided_by = decided_by
        self.grant_access(request.subject, request.scope, request.permission, decided_by)
        return request

    def get_audit_trail(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get access audit trail."""
        return [
            {
                "entry_id": e.entry_id,
                "subject": e.subject,
                "scope": e.scope,
                "operation": e.operation,
                "granted": e.granted,
                "timestamp": e.timestamp,
            }
            for e in self._audit[-limit:]
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_grants": len(self._grants),
            "total_requests": len(self._requests),
            "pending_requests": sum(1 for r in self._requests.values() if r.status == "pending"),
            "total_audit_entries": len(self._audit),
            "engine_status": "operational",
        }


engine = MemoryAccessController()


@router.post("/memora/access/grants")
async def grant_access(subject: str, scope: str, permission: str = "read",
                       granted_by: str = "system", ttl_seconds: float = 0.0):
    """Grant access to a memory scope."""
    grant_id = engine.grant_access(subject, scope, permission, granted_by, ttl_seconds)
    return {"grant_id": grant_id, "status": "granted"}


@router.delete("/memora/access/grants/{grant_id}")
async def revoke_access(grant_id: str):
    """Revoke an access grant."""
    if not engine.revoke_access(grant_id):
        raise HTTPException(status_code=404, detail="Grant not found")
    return {"grant_id": grant_id, "status": "revoked"}


@router.get("/memora/access/check")
async def check_access(subject: str, scope: str, permission: str = "read"):
    """Check if a subject has access to a scope."""
    granted = engine.check_access(subject, scope, permission)
    return {"subject": subject, "scope": scope, "permission": permission, "granted": granted}


@router.post("/memora/access/requests")
async def request_access(subject: str, scope: str, permission: str, reason: str = ""):
    """Request access to a memory scope."""
    request_id = engine.request_access(subject, scope, permission, reason)
    return {"request_id": request_id, "status": "pending"}


@router.post("/memora/access/requests/{request_id}/approve")
async def approve_request(request_id: str, decided_by: str = "system"):
    """Approve an access request."""
    request = engine.approve_request(request_id, decided_by)
    return {"request_id": request.request_id, "status": "approved"}


@router.get("/memora/access/audit")
async def get_audit_trail(limit: int = 50):
    """Get access audit trail."""
    return {"entries": engine.get_audit_trail(limit)}


@router.get("/memora/access/report")
async def access_report():
    """Get access controller report."""
    return engine.get_report()
