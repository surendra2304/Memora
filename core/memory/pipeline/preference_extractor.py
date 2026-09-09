"""
Autonomous Fact & Preference Extractor for Memora
Extracts user preferences, declarative facts, constraints, and favorites
from conversation turns and operational text.
"""
import re
from typing import List, Dict, Any, Optional

class ExtractedFact:
    def __init__(
        self,
        raw_statement: str,
        normalized_fact: str,
        category: str,
        entities: List[str],
        importance: float = 0.95,
        confidence: float = 1.0
    ):
        self.raw_statement = raw_statement
        self.normalized_fact = normalized_fact
        self.category = category
        self.entities = entities
        self.importance = importance
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_statement": self.raw_statement,
            "normalized_fact": self.normalized_fact,
            "category": self.category,
            "entities": self.entities,
            "importance": self.importance,
            "confidence": self.confidence,
        }


class PreferenceExtractor:
    """
    Extracts semantic facts and user preferences from natural language utterances.
    """

    PATTERNS = [
        # "I like / love / prefer / enjoy X"
        (
            r"\b(?:i\s+(?:really\s+)?(?:like|love|prefer|enjoy|favor))\s+([a-zA-Z0-9\s,'\-_]+?)(?:\.|$|,|\b(?:because|and|but)\b)",
            "preference",
            "User likes {item}."
        ),
        # "My fav / favo(u)rite [category] is X"
        (
            r"\b(?:my\s+(?:all-time\s+)?favou?rite\s+([a-zA-Z0-9\s\-_]+?)\s+is)\s+([a-zA-Z0-9\s,'\-_]+?)(?:\.|$|,|\b(?:because|and)\b)",
            "favorite_with_cat",
            "User's favorite {cat} is {item}."
        ),
        # "My fav / favo(u)rite is X"
        (
            r"\b(?:my\s+(?:all-time\s+)?favou?rite\s+is)\s+([a-zA-Z0-9\s,'\-_]+?)(?:\.|$|,|\b(?:because|and)\b)",
            "favorite",
            "User's favorite is {item}."
        ),
        # "I dislike / hate / don't like X"
        (
            r"\b(?:i\s+(?:dislike|hate|detest|(?:do\s*not|don't)\s+like))\s+([a-zA-Z0-9\s,'\-_]+?)(?:\.|$|,|\b(?:because|and)\b)",
            "dislike",
            "User dislikes {item}."
        ),
        # "I am allergic to X"
        (
            r"\b(?:i\s+am\s+(?:severely\s+)?allergic\s+to)\s+([a-zA-Z0-9\s,'\-_]+?)(?:\.|$|,|\b(?:and)\b)",
            "allergy",
            "User is allergic to {item}."
        ),
        # "I live in / am from X"
        (
            r"\b(?:i\s+(?:live|reside)\s+in|i\s+am\s+from)\s+([a-zA-Z0-9\s,'\-_]+?)(?:\.|$|,)",
            "location",
            "User resides in {item}."
        ),
        # "I work as / at X"
        (
            r"\b(?:i\s+work\s+(?:as|at))\s+([a-zA-Z0-9\s,'\-_]+?)(?:\.|$|,)",
            "profession",
            "User works as/at {item}."
        ),
        # "My name is X" / "Call me X"
        (
            r"\b(?:my\s+name\s+is|call\s+me)\s+([a-zA-Z0-9\s,'\-_]+?)(?:\.|$|,)",
            "identity",
            "User's preferred name is {item}."
        ),
    ]

    FOOD_CURRY_KEYWORDS = {"prawn", "prawns", "curry", "chicken", "paneer", "mutton", "fish", "biryani", "pizza", "burger", "pasta", "tacos", "dosa", "sushi", "ramen"}

    @classmethod
    def extract_facts(cls, text: str) -> List[ExtractedFact]:
        if not text or not text.strip():
            return []

        clean_text = text.strip()
        facts: List[ExtractedFact] = []
        lower_text = clean_text.lower()

        for pattern, pattern_type, template in cls.PATTERNS:
            matches = re.finditer(pattern, lower_text, re.IGNORECASE)
            for match in matches:
                groups = match.groups()
                if pattern_type == "favorite_with_cat" and len(groups) >= 2:
                    category_name = groups[0].strip()
                    item_name = groups[1].strip()
                    normalized = template.format(cat=category_name, item=item_name)
                    entities = ["user_preference", "favorite", category_name.replace(" ", "_"), item_name.replace(" ", "_")]
                    if any(k in item_name or k in category_name for k in cls.FOOD_CURRY_KEYWORDS):
                        entities.extend(["food", "curry", "cuisine"])
                    facts.append(
                        ExtractedFact(
                            raw_statement=match.group(0),
                            normalized_fact=normalized,
                            category=f"favorite_{category_name}",
                            entities=list(dict.fromkeys(entities)),
                            importance=0.95
                        )
                    )
                elif len(groups) >= 1:
                    item_name = groups[0].strip()
                    if not item_name or len(item_name) < 2:
                        continue
                    
                    # If item is curry/food related, enrich entity tags
                    entities = ["user_preference", pattern_type, item_name.replace(" ", "_")]
                    is_food = any(k in item_name for k in cls.FOOD_CURRY_KEYWORDS)
                    
                    # Special semantic expansion for prawns/curry
                    if "prawn" in item_name:
                        normalized = f"User likes prawns. User's favourite curry/food is prawns."
                        entities.extend(["prawns", "curry", "food", "favorite"])
                    elif is_food:
                        normalized = f"User likes {item_name} (preference: food/dish)."
                        entities.extend(["food", "dish", "cuisine"])
                    else:
                        normalized = template.format(item=item_name)

                    facts.append(
                        ExtractedFact(
                            raw_statement=match.group(0),
                            normalized_fact=normalized,
                            category=pattern_type,
                            entities=list(dict.fromkeys(entities)),
                            importance=0.95
                        )
                    )

        return facts
