"""
Entity and Relationship Extraction for Memora Write Pipeline
Extracts structured entities, concepts, and relational triples from memory content.
"""
import re
from typing import Dict, Any, List

class EntityExtractor:
    KNOWN_AGENTS = {"friday", "forge", "nexus", "intelx", "sentinel", "futuris", "ai_universe"}
    URI_PATTERN = re.compile(r"memora://[a-zA-Z0-9_\-\./]+")
    ACTION_VERBS = ["created", "built", "implemented", "deployed", "fixed", "optimized", "migrated", "verified", "rejected", "queried"]

    @classmethod
    def extract_entities_and_relationships(cls, text: str) -> Dict[str, Any]:
        text_lower = text.lower()
        
        # Extract agent entities
        agents_found = [agent for agent in cls.KNOWN_AGENTS if re.search(rf"\b{agent}\b", text_lower)]
        
        # Extract URI namespace paths
        uris_found = cls.URI_PATTERN.findall(text)
        
        # Extract keywords and components
        components = []
        for kw in ["fastapi", "postgresql", "redis", "qdrant", "sqlite", "docker", "alembic", "vector", "fts5"]:
            if kw in text_lower:
                components.append(kw)

        # Extract basic relational triples
        triples = []
        for verb in cls.ACTION_VERBS:
            if f" {verb} " in text_lower:
                parts = text_lower.split(f" {verb} ", 1)
                subject = parts[0].strip().split()[-1] if parts[0].strip() else "unknown"
                obj = parts[1].strip().split(".")[0] if parts[1].strip() else "unknown"
                triples.append({
                    "subject": subject,
                    "predicate": verb,
                    "object": obj[:64]
                })

        return {
            "agents": agents_found,
            "uris": uris_found,
            "components": components,
            "triples": triples,
            "word_count": len(text.split())
        }