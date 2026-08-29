"""
AI Universe Specialized Ecosystem Adapter for Memora
Provides model reasoning grounding against verified canonical memories to eliminate hallucinations.
"""
from typing import Optional, Dict, Any, List
import logging

from adapters.base_adapter import BaseAgentAdapter

logger = logging.getLogger(__name__)

class AIUniverseAdapter(BaseAgentAdapter):
    """
    Specialized adapter for AI Universe, grounding LLM reasoning against verified facts.
    """
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        default_namespace: str = "memora://universe/global",
        http_client: Optional[Any] = None
    ):
        super().__init__(
            agent_name="ai_universe",
            base_url=base_url,
            api_key=api_key,
            default_namespace=default_namespace,
            role="global",
            http_client=http_client
        )

    def ground_model_reasoning(
        self,
        prompt: str,
        agent_scope: Optional[str] = None,
        min_confidence: float = 0.85,
        max_facts: int = 10
    ) -> Dict[str, Any]:
        """
        Queries MEMORA for verified facts only (lifecycle_state == 'verified') within the authorized scope
        to ground reasoning models and prevent hallucination.
        """
        # Execute hybrid search across verified universe memories (min_score=0.0 for RRF scores)
        search_results = self.search_memories(
            query=prompt,
            namespace_path=agent_scope or self.default_namespace,
            limit=max_facts * 3,
            min_score=0.0,
            purpose="AI Universe model reasoning grounding"
        )

        # Filter strictly for verified lifecycle state and high confidence
        verified_facts = []
        citations = []
        for item in search_results:
            if item.get("lifecycle_state") == "verified" and item.get("confidence", 0.0) >= min_confidence:
                verified_facts.append({
                    "id": item.get("id"),
                    "fact": item.get("content_text"),
                    "confidence": item.get("confidence"),
                    "source": item.get("owner_name", "universe"),
                    "score": item.get("final_score")
                })
                citations.append({
                    "memory_id": item.get("id"),
                    "namespace": item.get("namespace_path"),
                    "timestamp": item.get("created_at")
                })
                if len(verified_facts) >= max_facts:
                    break

        # Calculate risk indicator
        if len(verified_facts) >= 2:
            hallucination_risk = "LOW"
        elif len(verified_facts) == 1:
            hallucination_risk = "MODERATE"
        else:
            hallucination_risk = "HIGH_UNGROUNDED"

        # Format grounded context text
        facts_text = "\n".join([f"- [Fact {i+1}] {f['fact']} (Confidence: {f['confidence']})" for i, f in enumerate(verified_facts)])
        grounded_prompt = (
            f"=== VERIFIED GROUNDING KNOWLEDGE (MEMORA) ===\n{facts_text or 'No verified knowledge available.'}\n"
            f"=== END GROUNDING ===\n\nTask: {prompt}"
        )

        return {
            "query": prompt,
            "grounded_prompt": grounded_prompt,
            "verified_facts_count": len(verified_facts),
            "verified_facts": verified_facts,
            "citations": citations,
            "hallucination_risk": hallucination_risk,
            "scope_applied": agent_scope or self.default_namespace
        }