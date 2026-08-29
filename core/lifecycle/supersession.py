"""
Contradiction and Supersession Engine for Memora
Enforces the core rule:
"Never resolve contradictions using recency alone; use provenance and confidence first."
"""
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from storage.relational.models import MemoryRecord, LifecycleState, Agent
from core.lifecycle.state_machine import MemoryLifecycleEngine

class ContradictionResolutionDecision:
    def __init__(
        self,
        winner_id: str,
        superseded_id: str,
        evidence_winner: float,
        evidence_loser: float,
        reason: str
    ):
        self.winner_id = winner_id
        self.superseded_id = superseded_id
        self.evidence_winner = evidence_winner
        self.evidence_loser = evidence_loser
        self.reason = reason

class SupersessionEngine:
    SOURCE_AUTHORITY_TIERS = {
        "friday": 1.0,
        "surendra": 1.0,
        "root": 1.0,
        "sentinel": 0.95,
        "intelx": 0.90,
        "futuris": 0.90,
        "forge": 0.85,
        "nexus": 0.85,
        "ai_universe": 0.80,
        "api": 0.60,
        "crawler": 0.50,
        "unknown": 0.30,
    }

    @classmethod
    def calculate_evidence_score(cls, record: MemoryRecord, owner_name: Optional[str] = None) -> float:
        """
        Computes composite evidence score from confidence (0.0-1.0),
        source/owner authority, and verification status.
        """
        source_key = (owner_name or record.source or "unknown").lower()
        authority = cls.SOURCE_AUTHORITY_TIERS.get(source_key, 0.5)

        # Verification bonus
        verification_bonus = 0.25 if record.lifecycle_state == LifecycleState.VERIFIED else 0.0

        # Provenance richness bonus
        prov_bonus = 0.1 if record.provenance and len(record.provenance) > 1 else 0.0

        # Composite score
        evidence = (record.confidence * 0.55) + (authority * 0.30) + verification_bonus + prov_bonus
        return round(min(1.0, evidence), 4)

    @classmethod
    def resolve_contradiction_and_supersede(
        cls,
        db: Session,
        existing_record: MemoryRecord,
        new_record: MemoryRecord,
        existing_owner_name: Optional[str] = None,
        new_owner_name: Optional[str] = None,
        reason: Optional[str] = None
    ) -> ContradictionResolutionDecision:
        """
        Resolves contradiction between two records based on evidence rather than recency.
        """
        score_existing = cls.calculate_evidence_score(existing_record, existing_owner_name)
        score_new = cls.calculate_evidence_score(new_record, new_owner_name)

        if score_new >= score_existing:
            # New memory supersedes existing memory
            MemoryLifecycleEngine.transition(
                existing_record,
                target_state=LifecycleState.SUPERSEDED,
                superseded_by_id=new_record.id
            )
            # Ensure new memory is active
            if new_record.lifecycle_state == LifecycleState.CANDIDATE:
                MemoryLifecycleEngine.transition(new_record, target_state=LifecycleState.ACTIVE)

            db.commit()
            db.refresh(existing_record)
            db.refresh(new_record)

            return ContradictionResolutionDecision(
                winner_id=new_record.id,
                superseded_id=existing_record.id,
                evidence_winner=score_new,
                evidence_loser=score_existing,
                reason=reason or f"New memory (evidence: {score_new}) superseded older record (evidence: {score_existing}) based on confidence & provenance authority."
            )
        else:
            # Existing memory has higher evidence authority; new record is subordinated or candidate
            MemoryLifecycleEngine.transition(
                new_record,
                target_state=LifecycleState.SUPERSEDED,
                superseded_by_id=existing_record.id
            )
            db.commit()
            db.refresh(existing_record)
            db.refresh(new_record)

            return ContradictionResolutionDecision(
                winner_id=existing_record.id,
                superseded_id=new_record.id,
                evidence_winner=score_existing,
                evidence_loser=score_new,
                reason=reason or f"Existing verified memory (evidence: {score_existing}) retained authority over new candidate (evidence: {score_new}) per provenance precedence."
            )