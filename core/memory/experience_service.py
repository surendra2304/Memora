"""
Experience Learning Service for Memora
Synthesizes batches of past task outcomes (successes and failures) into high-importance
Experience memories (MemoryType.EXPERIENCE) providing predictive operational guidelines and failure mode alerts.
"""
import os
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
    domain: Optional[str] = Field(default=None, description="Functional domain e.g. 'deployment', 'auth', 'database'")

class LearnExperienceRequest(BaseModel):
    agent_id: Optional[str] = Field(default=None, description="Agent ID or name")
    namespace_path: Optional[str] = Field(default=None, description="Namespace to store experience memories")
    outcomes: List[TaskOutcome] = Field(..., min_length=1, description="List of task outcomes to extract lessons from")

class ExperienceLearnerService:
    @classmethod
    def synthesize_experience(cls, outcomes: List[TaskOutcome]) -> str:
        """
        Uses LLM or semantic heuristic engine to synthesize failure modes and best practices.
        """
        failures = [o for o in outcomes if o.status.lower() in ["failure", "error", "crashed"]]
        successes = [o for o in outcomes if o.status.lower() in ["success", "passed", "completed"]]

        # 1. Try OpenAI client if available
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                import openai
                client = openai.OpenAI(api_key=openai_key)
                prompt = (
                    "Analyze the following agent task outcomes and synthesize a concise, high-impact "
                    "operational experience memory containing:\n"
                    "1. Typical failure modes and triggers.\n"
                    "2. Enforceable best practices and mandatory pre-checks.\n\n"
                    f"Failures: {failures}\nSuccesses: {successes}"
                )
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=300
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(f"OpenAI experience extraction failed ({e}). Using local synthesis.")

        # 2. Local High-Density Semantic Synthesis
        lessons = []
        if failures:
            f_summary = []
            for f in failures:
                domain = f.domain or f.task_name
                err = f.error_log or "unexpected failure during execution"
                f_summary.append(f"In task '{domain}', failure occurred: {err}")
            lessons.append(f"[Failure Mode Warning]: {' '.join(f_summary)}")

            remediation = "Mandatory operational guideline: Always execute required pre-flight validations, schema migrations, and health checks before initiating execution."
            lessons.append(f"[Best Practice]: {remediation}")

        if successes and not failures:
            s_summary = [f"Task '{s.task_name}' succeeded under parameters: {s.context or s.actions_taken or 'standard configuration'}." for s in successes]
            lessons.append(f"[Proven Success Pattern]: {' '.join(s_summary)}")

        return " ".join(lessons)

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