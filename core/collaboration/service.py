"""
Multi-agent collaboration for Memora.

Memora already had the primitives for collaboration — namespaces, access grants,
a share endpoint — but nothing that let one agent *ask* for help. An agent that
hit a problem had no way to discover which peer held relevant experience, and a
peer with the answer had no way to offer it.

This service adds three real interactions:

  request_assistance   an agent describes a problem; the service finds peers who
                       hold relevant knowledge and returns them as candidates,
                       together with whatever those peers have already made
                       shareable. Nothing private is disclosed: candidates are
                       identified from the asker's *own* visible corpus plus
                       explicitly shared material.

  contribute           a peer offers one of its memories to help, which creates a
                       scoped, time-boxed grant and an audited share. The owner
                       decides; the service never exposes a memory the owner has
                       not offered.

  delegate             hand a task to a sub-agent with a bounded scope, recording
                       the delegation so the outcome can be traced.

Every path goes through PolicyEngine, so collaboration cannot become a way around
the isolation rules that the rest of the system enforces.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.identity.service import IdentityService
from core.memory.search_service import SearchService
from core.policy.engine import PolicyDecision, PolicyEngine
from storage.relational.models import (
    AccessGrant,
    Agent,
    LifecycleState,
    MemoryRecord,
    Namespace,
)

logger = logging.getLogger(__name__)


class CollaborationError(Exception):
    """Base for collaboration failures."""


class CollaborationPermissionError(CollaborationError):
    """The caller is known but is not allowed to do this.

    Kept distinct from CollaborationError so the API can return 403 instead of
    404: classifying an authorisation refusal as a missing resource hides the
    real reason from the caller and from the audit trail.
    """
    """Raised when a collaboration request cannot be honoured."""


@dataclass
class AssistanceCandidate:
    """A peer who may be able to help, and why."""

    agent_id: str
    agent_name: str
    reason: str
    evidence_count: int = 0
    shareable_memory_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "reason": self.reason,
            "evidence_count": self.evidence_count,
            "shareable_memory_ids": self.shareable_memory_ids,
        }


@dataclass
class AssistanceResponse:
    """Result of an assistance request."""

    requester: str
    query: str
    candidates: List[AssistanceCandidate] = field(default_factory=list)
    immediately_usable: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requester": self.requester,
            "query": self.query,
            "candidate_count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates],
            "immediately_usable": self.immediately_usable,
            "created_at": self.created_at,
        }


def _namespace_path(db: Session, namespace_id: str, tenant_id: str, prefix: str) -> bool:
    """True when the namespace's path starts with the peer's bounded scope."""
    row = (
        db.query(Namespace.path)
        .filter(Namespace.id == namespace_id, Namespace.tenant_id == tenant_id)
        .first()
    )
    return bool(row and row[0].startswith(prefix))


class CollaborationService:
    """Coordinates help between agents without breaching namespace isolation."""

    #: How long a contribution grant lasts by default. Collaboration should be
    #: scoped and expire, not permanently widen access.
    DEFAULT_GRANT_HOURS = 24

    # ------------------------------------------------------------------ ask
    @classmethod
    def request_assistance(
        cls,
        db: Session,
        requester_name: str,
        query: str,
        purpose: str = "collaboration",
        limit: int = 5,
        tenant_id: Optional[str] = None,
    ) -> AssistanceResponse:
        """Find peers who can help with `query`.

        Two sources, both safe:
          1. Material the requester can ALREADY read (their own namespaces plus
             anything explicitly granted). This is returned as immediately
             usable, because no new access is created.
          2. Candidate peers, identified WITHOUT reading their private content:
             we look at which agents own memories in namespaces the requester has
             a grant to, and at agents whose role/domain matches the query terms.
             Only ids and names are disclosed, never content.
        """
        requester = IdentityService.get_agent_by_name(db, requester_name, tenant_id=tenant_id)
        if not requester:
            requester = IdentityService.register_agent(db, requester_name, tenant_id=tenant_id or "default")

        response = AssistanceResponse(requester=requester.name, query=query)

        # --- 1. what the requester can already use -------------------------
        visible = SearchService.hybrid_search(
            db=db,
            query_text=query,
            actor_name=requester.name,
            tenant_id=requester.tenant_id,
            purpose=purpose,
            limit=limit,
        )
        for item in visible:
            decision = PolicyEngine.evaluate_access(
                db,
                actor=requester,
                namespace=item.record.namespace,
                action="read",
                purpose=purpose,
                memory_id=item.record.id,
                log_audit=False,
            )
            if not decision.allowed:
                continue
            response.immediately_usable.append(
                {
                    "memory_id": item.record.id,
                    "content_text": item.record.content_text,
                    "memory_type": item.record.memory_type.value,
                    "owner_id": item.record.owner_id,
                    "namespace_path": getattr(item.record.namespace, "path", None),
                    "score": round(float(item.final_score), 4),
                }
            )

        # --- 2. who else might help ---------------------------------------
        # Peers are identified from grants the requester already holds and from
        # role/domain matches. No private content is read to build this list.
        granted_namespace_ids = {
            row[0]
            for row in db.query(AccessGrant.namespace_id)
            .filter(AccessGrant.agent_id == requester.id)
            .all()
        }
        owners_of_granted: Dict[str, int] = {}
        if granted_namespace_ids:
            for owner_id, count in (
                db.query(MemoryRecord.owner_id, db.query(MemoryRecord).count())
                .filter(
                    MemoryRecord.namespace_id.in_(list(granted_namespace_ids)),
                    MemoryRecord.lifecycle_state.in_(
                        [LifecycleState.ACTIVE, LifecycleState.VERIFIED]
                    ),
                )
                .group_by(MemoryRecord.owner_id)
                .all()
            ):
                owners_of_granted[owner_id] = count

        query_terms = {t for t in query.lower().split() if len(t) > 3}

        peers = (
            db.query(Agent)
            .filter(Agent.id != requester.id, Agent.tenant_id == requester.tenant_id)
            .all()
        )
        for peer in peers:
            reasons: List[str] = []
            evidence = 0
            shareable: List[str] = []

            if peer.id in owners_of_granted:
                evidence = owners_of_granted[peer.id]
                reasons.append(
                    f"owns {evidence} memories in namespaces already granted to you"
                )
                shareable = [
                    row[0]
                    for row in db.query(MemoryRecord.id)
                    .filter(
                        MemoryRecord.owner_id == peer.id,
                        MemoryRecord.namespace_id.in_(list(granted_namespace_ids)),
                        MemoryRecord.lifecycle_state.in_(
                            [LifecycleState.ACTIVE, LifecycleState.VERIFIED]
                        ),
                    )
                    .limit(limit)
                    .all()
                ]

            # Only role and description are free text. bounded_scope is an
            # enforced namespace-path prefix (PolicyEngine RULE_3), not a
            # description, so matching query words against it produced nonsense
            # and implied access the peer does not have.
            descriptor_terms = {
                t
                for t in " ".join(filter(None, [peer.role, peer.description])).lower().split()
                if len(t) > 3
            }
            overlap = query_terms & descriptor_terms
            if overlap:
                reasons.append(f"role/description matches: {', '.join(sorted(overlap))}")

            # A peer whose bounded scope covers a namespace the asker already has
            # a grant to is a genuinely useful collaborator.
            if peer.bounded_scope:
                covered = [
                    ns_id
                    for ns_id in granted_namespace_ids
                    if _namespace_path(db, ns_id, requester.tenant_id, peer.bounded_scope)
                ]
                if covered:
                    reasons.append(
                        f"bounded to '{peer.bounded_scope}', which covers "
                        f"{len(covered)} namespace(s) you can already reach"
                    )
                    evidence += len(covered)

            if reasons:
                response.candidates.append(
                    AssistanceCandidate(
                        agent_id=peer.id,
                        agent_name=peer.name,
                        reason="; ".join(reasons),
                        evidence_count=evidence,
                        shareable_memory_ids=shareable,
                    )
                )

        # Most evidence first; deterministic tiebreak on name.
        response.candidates.sort(key=lambda c: (-c.evidence_count, c.agent_name))
        response.candidates = response.candidates[:limit]
        return response

    # ------------------------------------------------------------- contribute
    @classmethod
    def contribute(
        cls,
        db: Session,
        contributor_name: str,
        memory_id: str,
        recipient_name: str,
        purpose: str = "collaboration",
        actions: Optional[List[str]] = None,
        ttl_hours: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Offer one memory to a peer: creates a scoped, expiring grant.

        Only the owner may contribute. The grant is time-boxed by default so
        helping once does not permanently widen another agent's access.
        """
        contributor = IdentityService.get_agent_by_name(db, contributor_name, tenant_id=tenant_id)
        if not contributor:
            raise CollaborationError(f"unknown contributor '{contributor_name}'")

        recipient = IdentityService.get_agent_by_name(db, recipient_name, tenant_id=tenant_id)
        if not recipient:
            raise CollaborationError(f"unknown recipient '{recipient_name}'")
        if recipient.id == contributor.id:
            raise CollaborationPermissionError(
                "an agent cannot contribute to itself"
            )

        record = db.query(MemoryRecord).filter(MemoryRecord.id == memory_id).first()
        if not record:
            raise CollaborationError(f"memory '{memory_id}' not found")
        if record.owner_id != contributor.id:
            # Never let an agent share someone else's memory. This is an
            # authorisation failure, not a missing resource, so it raises the
            # distinct type that lets callers answer 403 rather than 404.
            raise CollaborationPermissionError(
                f"'{contributor_name}' does not own memory '{memory_id}'"
            )

        namespace = db.query(Namespace).filter(Namespace.id == record.namespace_id).first()
        if not namespace:
            raise CollaborationError(f"namespace for memory '{memory_id}' not found")

        grant = IdentityService.grant_access(
            db,
            agent_id=recipient.id,
            namespace_id=namespace.id,
            actions=actions or ["read", "query"],
            purpose=purpose,
            ttl_hours=ttl_hours or cls.DEFAULT_GRANT_HOURS,
            tenant_id=record.tenant_id,
        )

        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                True,
                f"Memory contributed by {contributor.name} to {recipient.name}",
                "COLLABORATION_CONTRIBUTE",
            ),
            actor_id=contributor.id,
            memory_id=memory_id,
            tenant_id=record.tenant_id,
        )
        db.commit()

        return {
            "status": "contributed",
            "memory_id": memory_id,
            "from": contributor.name,
            "to": recipient.name,
            "grant_id": grant.id,
            "actions": grant.actions,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        }

    # ------------------------------------------------------------- delegate
    @classmethod
    def delegate(
        cls,
        db: Session,
        delegator_name: str,
        subagent_name: str,
        task_description: str,
        bounded_scope: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Hand a task to a sub-agent with a bounded scope."""
        delegator = IdentityService.get_agent_by_name(db, delegator_name, tenant_id=tenant_id)
        if not delegator:
            raise CollaborationError(f"unknown delegator '{delegator_name}'")

        scope = bounded_scope or f"memora://{delegator.name}/delegated/{subagent_name}"
        subagent = IdentityService.register_subagent(
            db,
            parent_agent_name=delegator.name,
            subagent_name=subagent_name,
            bounded_scope=scope,
            tenant_id=delegator.tenant_id,
        )

        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                True,
                f"Delegated to {subagent_name}: {task_description[:120]}",
                "COLLABORATION_DELEGATE",
            ),
            actor_id=delegator.id,
            tenant_id=delegator.tenant_id,
        )
        db.commit()

        return {
            "status": "delegated",
            "delegator": delegator.name,
            "subagent": subagent.name,
            "subagent_id": subagent.id,
            "bounded_scope": subagent.bounded_scope,
            "task": task_description,
        }


collaboration_service = CollaborationService()
