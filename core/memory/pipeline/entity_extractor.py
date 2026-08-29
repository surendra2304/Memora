"""
Deep Semantic Entity and Relationship Extraction Engine for Memora
Extracts typed Named Entities (Technologies, People, Organizations, Dates, Modules, Agents, Concepts)
and semantic Subject-Predicate-Object (SPO) relationship triples.
Supports local rule-based deep semantic parsing and optional LLM API fallback.
"""
import re
from typing import Dict, Any, List, Optional, Set, Tuple
import logging

logger = logging.getLogger(__name__)

class EntityExtractor:
    """
    Advanced Semantic Entity and Relationship Extractor for MEMORA.
    """
    # -------------------------------------------------------------
    # 1. CANONICAL GAZETTEER ONTOLOGY & SYNONYMS
    # -------------------------------------------------------------
    CANONICAL_ENTITIES: Dict[str, Set[str]] = {
        "postgresql": {"postgresql", "postgres", "pg_db", "pg-database", "postgres_db", "psql"},
        "redis": {"redis", "redis_cache", "redis_pubsub", "redis_queue", "redis-server"},
        "qdrant": {"qdrant", "qdrant_db", "qdrant_vector", "qdrant_adapter"},
        "sqlite": {"sqlite", "sqlite3", "sqlite_db", "sqlite_fallback"},
        "fastapi": {"fastapi", "fast_api", "fastapi_gateway", "fastapi_api"},
        "docker": {"docker", "docker_compose", "docker-compose", "containerd"},
        "alembic": {"alembic", "alembic_migrations", "database_migrations"},
        "sqlalchemy": {"sqlalchemy", "sqlalchemy_orm", "sqlmodel"},
        "vite": {"vite", "vitejs", "vite_bundler"},
        "tailwind": {"tailwind", "tailwindcss"},
        "kubernetes": {"kubernetes", "k8s", "k8s_cluster", "kube"},
        "argon2": {"argon2", "argon2id", "password_hash"},
        "python": {"python", "python3", "cpython"},
        "pydantic": {"pydantic", "pydantic_v2"},
        "kafka": {"kafka", "apache_kafka"},
        "graphql": {"graphql", "gql"},
        "auth_module": {"auth.py", "auth-module", "auth_module", "auth_service", "authentication_handler"}
    }

    # Reverse lookup map: alias -> canonical name
    ALIAS_TO_CANONICAL: Dict[str, str] = {}
    for canonical, aliases in CANONICAL_ENTITIES.items():
        for alias in aliases:
            ALIAS_TO_CANONICAL[alias.lower()] = canonical

    # Typed Classifications
    KNOWN_PEOPLE = {
        "surendra", "guido van rossum", "demis hassabis", "sam altman", "dario amodei",
        "yann lecun", "geoffrey hinton", "andrew ng", "satya nadella", "ilya sutskever"
    }

    KNOWN_ORGANIZATIONS = {
        "google deepmind", "deepmind", "openai", "anthropic", "meta", "microsoft",
        "aws", "amazon", "huggingface", "friday universe", "memora team", "apple"
    }

    KNOWN_AGENTS = {
        "friday", "forge", "nexus", "intelx", "sentinel", "futuris", "mt5", "ai_universe"
    }

    KNOWN_TECHNOLOGIES = {
        "postgresql", "postgres", "pg_db", "redis", "qdrant", "sqlite", "fastapi", "docker",
        "alembic", "sqlalchemy", "vite", "tailwind", "kubernetes", "k8s", "argon2", "python",
        "pydantic", "kafka", "graphql", "sql", "orm", "fts5", "pg_trgm", "vector_search"
    }

    KNOWN_CONCEPTS = {
        "named entity recognition", "ner", "knowledge graph", "reciprocal rank fusion",
        "rrf", "access control", "sql injection", "bounded context", "token budgeting",
        "supersession", "memory decay", "secret scanning", "context bundle", "encryption",
        "connection pooling", "parameterized queries", "reactive ui"
    }

    DATE_REGEX = re.compile(
        r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2},? \d{4}|q[1-4] \d{4}|today|yesterday)\b",
        re.IGNORECASE
    )

    MODULE_REGEX = re.compile(r"\b[a-zA-Z0-9_\-]+\.(?:py|ts|js|json|yaml|yml|sql|md)\b|\bmemora://[a-zA-Z0-9_\-\./]+\b")
    URI_PATTERN = re.compile(r"memora://[a-zA-Z0-9_\-\./]+")

    # Triple extraction predicates
    ACTION_PREDICATES = [
        "implemented", "designed", "built", "created", "verified", "scanned", "discovered",
        "promoted", "delegated", "stores", "indexes", "caches", "secures", "audited",
        "migrated", "rejected", "configured", "bundles", "connects_to", "depends_on", "deployed", "fixed", "optimized"
    ]

    @classmethod
    def resolve_canonical(cls, entity_name: str) -> str:
        """Resolves an entity or alias to its canonical representation."""
        clean = entity_name.strip().lower()
        return cls.ALIAS_TO_CANONICAL.get(clean, clean)

    @classmethod
    def extract_entities_and_relationships(cls, text: str) -> Dict[str, Any]:
        """
        Deep semantic extraction of typed entities, resolved canonical names, and SPO relationship triples.
        """
        text_lower = text.lower()
        
        # 1. Dates
        dates_found = list(set(cls.DATE_REGEX.findall(text)))

        # 2. Modules & URIs
        modules_found = list(set(cls.MODULE_REGEX.findall(text)))
        uris_found = list(set(cls.URI_PATTERN.findall(text)))

        # 3. People
        people_found = []
        for person in cls.KNOWN_PEOPLE:
            if re.search(rf"\b{re.escape(person)}\b", text_lower):
                people_found.append(person.title())

        # 4. Organizations
        orgs_found = []
        for org in cls.KNOWN_ORGANIZATIONS:
            if re.search(rf"\b{re.escape(org)}\b", text_lower):
                orgs_found.append(org.title())

        # 5. Agents
        agents_found = []
        raw_agents_lower = []
        for agent in cls.KNOWN_AGENTS:
            if re.search(rf"\b{re.escape(agent)}\b", text_lower):
                agents_found.append(agent.upper())
                raw_agents_lower.append(agent)

        # 6. Technologies & Canonical Resolution
        techs_found = set()
        resolved_entities = set()
        for tech in cls.KNOWN_TECHNOLOGIES:
            if re.search(rf"\b{re.escape(tech)}\b", text_lower):
                techs_found.add(tech)
                resolved_entities.add(cls.resolve_canonical(tech))

        # Check aliases
        for alias, canonical in cls.ALIAS_TO_CANONICAL.items():
            if re.search(rf"\b{re.escape(alias)}\b", text_lower):
                techs_found.add(alias)
                resolved_entities.add(canonical)

        # 7. Concepts
        concepts_found = []
        for concept in cls.KNOWN_CONCEPTS:
            if re.search(rf"\b{re.escape(concept)}\b", text_lower):
                concepts_found.append(concept)
                resolved_entities.add(cls.resolve_canonical(concept))

        # 8. Complex SPO Triples Extraction
        triples = cls._extract_semantic_triples(text)

        # Combine all entities into flat list for indexing
        all_entities = list(
            set([cls.resolve_canonical(e) for e in list(techs_found) + concepts_found + [p.lower() for p in people_found] + [a.lower() for a in agents_found]])
        )

        return {
            # Backward-compatible top-level keys
            "agents": raw_agents_lower,
            "uris": uris_found,
            "components": sorted(list(techs_found)),
            "entities": all_entities,
            "resolved_canonical_entities": list(resolved_entities),
            "typed_entities": {
                "technologies": sorted(list(techs_found)),
                "people": sorted(people_found),
                "organizations": sorted(orgs_found),
                "agents": sorted(agents_found),
                "dates": sorted(dates_found),
                "modules": sorted(modules_found),
                "concepts": sorted(concepts_found)
            },
            "triples": triples,
            "word_count": len(text.split()),
            "character_count": len(text)
        }

    @classmethod
    def _extract_semantic_triples(cls, text: str) -> List[Dict[str, Any]]:
        """
        Extracts semantic Subject-Predicate-Object (SPO) relationship triples across sentences.
        """
        triples = []
        sentences = re.split(r"[.!?;\n]+", text)

        for sentence in sentences:
            sentence_clean = sentence.strip()
            if not sentence_clean:
                continue
            sentence_lower = sentence_clean.lower()

            for predicate in cls.ACTION_PREDICATES:
                # Look for predicate as isolated word
                pattern = rf"\b(\w[\w\s\-]{{1,35}}?)\s+{re.escape(predicate)}\s+([\w\s\-]{{2,60}})"
                matches = re.findall(pattern, sentence_lower)
                for match in matches:
                    raw_subj, raw_obj = match[0].strip(), match[1].strip()
                    # Filter stop words from subject tail
                    subj_tokens = raw_subj.split()
                    subj = subj_tokens[-2] + " " + subj_tokens[-1] if len(subj_tokens) >= 2 else (subj_tokens[0] if subj_tokens else "unknown")
                    
                    obj = raw_obj.split(",")[0].strip()
                    if obj.startswith("the "):
                        obj = obj[4:]
                    if obj.startswith("a "):
                        obj = obj[2:]
                    if obj.startswith("an "):
                        obj = obj[3:]

                    if subj and obj and len(subj) > 1 and len(obj) > 1:
                        triples.append({
                            "subject": cls.resolve_canonical(subj),
                            "predicate": predicate,
                            "object": cls.resolve_canonical(obj[:50]),
                            "confidence": 0.95
                        })

        # Deduplicate triples
        unique_triples = []
        seen = set()
        for t in triples:
            key = (t["subject"], t["predicate"], t["object"])
            if key not in seen:
                seen.add(key)
                unique_triples.append(t)

        return unique_triples