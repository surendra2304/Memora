"""
Experience Learning & Self-Upgrading Service for Memora
Synthesizes task outcomes (successes and failures) into high-importance
Experience memories (MemoryType.EXPERIENCE) providing predictive operational guidelines,
failure mode alerts, and automated behavioral adaptations across all 9 agents.
"""
import os
import re
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from storage.relational.models import MemoryRecord, MemoryType, LifecycleState
from core.identity.service import IdentityService
from core.memory.pipeline.write_service import MemoryWriteService

logger = logging.getLogger(__name__)

class TaskOutcome(BaseModel):
    task_name: str = Field(..., description="Name or domain of the task executed")
    status: str = Field(..., description="Outcome status: 'failure' or 'success'")
    error_log: Optional[str] = Field(default=None, description="Error messages or crash logs")
    actions_taken: Optional[str] = Field(default=None, description="Sequence of actions or tool calls made")
    context: Optional[str] = Field(default=None, description="Operational environment or context details")
    domain: Optional[str] = Field(default=None, description="Functional domain e.g. 'tool_execution', 'code_synthesis', 'trading'")

class LearnExperienceRequest(BaseModel):
    agent_id: Optional[str] = Field(default=None, description="Agent ID or name")
    namespace_path: Optional[str] = Field(default=None, description="Namespace to store experience memories")
    outcomes: List[TaskOutcome] = Field(..., min_length=1, description="List of task outcomes to extract lessons from")

class ExperienceLearnerService:
    @classmethod
    def extract_remediation_rule(cls, task_name: str, error_log: str, domain: Optional[str] = None) -> str:
        """
        Derives concrete, actionable prevention rules from specific error signatures.
        """
        err_lower = (error_log or "").lower()
        dom = domain or task_name

        if "permission" in err_lower or "access denied" in err_lower or "elevat" in err_lower:
            return f"Verify process security privilege and administrator execution rights before running '{task_name}'."
        elif "not found" in err_lower or "no such file" in err_lower or "path" in err_lower:
            return f"Validate absolute filesystem paths and ensure target directory/file exists prior to executing '{task_name}'."
        elif "syntax" in err_lower or "unexpected token" in err_lower or "parse" in err_lower:
            return f"Ensure strict parameter quote escaping and schema validation before dispatching '{task_name}'."
        elif "timeout" in err_lower or "timed out" in err_lower or "deadline" in err_lower:
            return f"Increase request timeout budget and configure exponential backoff retries when calling '{task_name}'."
        elif "connection refused" in err_lower or "connect" in err_lower or "unreachable" in err_lower:
            return f"Check endpoint health status and verify socket/service availability before connecting in '{task_name}'."
        elif "rate limit" in err_lower or "429" in err_lower or "too many requests" in err_lower:
            return f"Apply rate limiter and automatically fallback to secondary provider gateway when running '{task_name}'."
        elif "import" in err_lower or "module" in err_lower or "dependency" in err_lower:
            return f"Inspect dependency environment and ensure required package is installed before launching '{task_name}'."
        elif "drawdown" in err_lower or "slippage" in err_lower or "volatil" in err_lower:
            return f"Reduce position sizing by 50% and enforce tighter stop-loss guardrails during high volatility in '{task_name}'."
        else:
            return f"Execute pre-flight parameter verification and handle graceful exceptions when executing '{task_name}'."

    @classmethod
    def synthesize_experience(cls, outcomes: List[TaskOutcome]) -> str:
        """
        Synthesizes failure modes and operational best practices from a batch of outcomes.
        """
        failures = [o for o in outcomes if o.status.lower() in ["failure", "error", "crashed"]]
        successes = [o for o in outcomes if o.status.lower() in ["success", "passed", "completed"]]

        lessons = []
        if failures:
            for f in failures:
                domain = f.domain or f.task_name
                err = (f.error_log or "unexpected failure").strip()
                remediation = cls.extract_remediation_rule(f.task_name, err, domain)
                lessons.append(
                    f"[FAILURE WARNING / Failure Mode Warning in '{domain}'] Trigger: {err[:150]}. "
                    f"[LEARNED BEST PRACTICE]: {remediation}"
                )

        if successes and not failures:
            for s in successes:
                lessons.append(
                    f"[PROVEN SUCCESS PATTERN in '{s.domain or s.task_name}'] "
                    f"Configuration '{s.context or s.actions_taken or 'standard'}' succeeded. Replicate this strategy."
                )

        return " ".join(lessons) if lessons else "Operational outcome logged with standard baselines."

    @classmethod
    def learn_experience(
        cls,
        db: Session,
        actor_name: str,
        outcomes: List[TaskOutcome],
        namespace_path: Optional[str] = None
    ) -> MemoryRecord:
        """
        Extracts operational experience and writes it through the 10-step Write Pipeline
        as a high-importance MemoryType.EXPERIENCE record.
        """
        actor = IdentityService.get_agent_by_name(db, actor_name)
        if not actor:
            actor = IdentityService.register_agent(db, actor_name)

        target_ns = namespace_path or f"memora://{actor.name}/private"
        synthesized_text = cls.synthesize_experience(outcomes)
        domains = list(set([o.domain or o.task_name for o in outcomes]))

        result = MemoryWriteService.execute_pipeline(
            db=db,
            actor_name=actor.name,
            content_text=synthesized_text,
            target_namespace_path=target_ns,
            memory_type=MemoryType.EXPERIENCE,
            confidence=0.99,
            importance=0.99,
            provenance={
                "experience_domains": domains,
                "outcomes_analyzed": len(outcomes),
                "failure_count": len([o for o in outcomes if o.status.lower() == "failure"]),
                "success_count": len([o for o in outcomes if o.status.lower() == "success"]),
                "learning_timestamp": datetime.now(timezone.utc).isoformat()
            }
        )

        return result.record

    @classmethod
    def learn_single_outcome(
        cls,
        db: Session,
        actor_name: str,
        task_name: str,
        status: str,
        error_log: Optional[str] = None,
        actions_taken: Optional[str] = None,
        context: Optional[str] = None,
        domain: Optional[str] = None,
        namespace_path: Optional[str] = None
    ) -> MemoryRecord:
        """
        Fast single-outcome learning for immediate real-time adaptation after any action.
        """
        outcome = TaskOutcome(
            task_name=task_name,
            status=status,
            error_log=error_log,
            actions_taken=actions_taken,
            context=context,
            domain=domain or "operational"
        )
        return cls.learn_experience(
            db=db,
            actor_name=actor_name,
            outcomes=[outcome],
            namespace_path=namespace_path
        )

    @classmethod
    def get_active_experiences(
        cls,
        db: Session,
        actor_name: str,
        domain: Optional[str] = None,
        limit: int = 5
    ) -> List[MemoryRecord]:
        """
        Retrieves top learned guidelines and experience records for a specific agent.
        """
        actor = IdentityService.get_agent_by_name(db, actor_name)
        if not actor:
            return []

        query = db.query(MemoryRecord).filter(
            MemoryRecord.owner_id == actor.id,
            MemoryRecord.memory_type == MemoryType.EXPERIENCE,
            MemoryRecord.lifecycle_state == LifecycleState.ACTIVE
        ).order_by(MemoryRecord.importance.desc(), MemoryRecord.created_at.desc())

        records = query.limit(limit * 2).all()
        if domain:
            filtered = [
                r for r in records
                if domain.lower() in r.content_text.lower() or (r.provenance and domain.lower() in str(r.provenance).lower())
            ]
            if filtered:
                return filtered[:limit]

        return records[:limit]