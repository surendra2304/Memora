"""
Poison Memory and Prompt Injection Detector for Memora
Prevents adversarial injections, prompt overrides, and malicious payloads
from poisoning the episodic or semantic memory fabric.
"""
import re
from typing import List, Tuple
import logging

logger = logging.getLogger(__name__)

class PoisonMemoryViolation(Exception):
    """Raised when memory content contains prompt injections or adversarial poison vectors."""
    def __init__(self, message: str, detected_patterns: List[str]):
        super().__init__(message)
        self.message = message
        self.detected_patterns = detected_patterns

class PoisonDetector:
    POISON_PATTERNS: List[Tuple[str, str]] = [
        (r"ignore\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions|commands|prompts|directives)", "Instruction Override / Ignore Previous"),
        (r"disregard\s+(?:all\s+)?(?:previous|prior|safety)\s+(?:instructions|guidelines|rules|constraints)", "Instruction Override / Disregard Guidelines"),
        (r"system\s+prompt\s+override", "System Prompt Override Attempt"),
        (r"you\s+are\s+now\s+(?:in\s+developer\s+mode|dan\b|unfiltered\s+ai|jailbroken)", "Persona / Jailbreak Hijack"),
        (r"(?:bypass|disable)\s+(?:safety|security|policy|guardrails|filters)", "Security Policy Bypass Attempt"),
        (r"(?:reveal|exfiltrate|leak|dump)\s+(?:system\s+prompt|master\s+key|api\s+secrets)", "Secret Exfiltration Vector"),
        (r"<script\b[^>]*>[\s\S]*?<\/script>", "XSS / Script Tag Injection"),
        (r"\|\s*bash\b|;\s*rm\s+-rf\b|DROP\s+TABLE\b|TRUNCATE\s+TABLE\b", "Command / SQL Injection Vector"),
    ]

    _COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), name) for p, name in POISON_PATTERNS]

    @classmethod
    def scan_content(cls, content: str) -> List[str]:
        if not content:
            return []
        detected = []
        for regex, name in cls._COMPILED_PATTERNS:
            if regex.search(content):
                detected.append(name)
        return detected

    @classmethod
    def validate_content_safety(cls, content: str) -> None:
        detected = cls.scan_content(content)
        if detected:
            msg = f"PoisonMemoryViolation: Content contains adversarial injection vectors: {', '.join(detected)}"
            logger.warning(msg)
            raise PoisonMemoryViolation(msg, detected)
