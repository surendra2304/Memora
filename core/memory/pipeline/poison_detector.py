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
        # Command / SQL injection. This used to match the bare keywords
        # `DROP TABLE`, `TRUNCATE TABLE` and `| bash`, which blocked legitimate
        # engineering memories such as "the migration runs DROP TABLE on
        # audit_staging" or "we bootstrap nodes with curl … | bash" — precisely
        # the kind of operational knowledge this store exists to hold.
        #
        # It now requires injection *syntax*: a quote/semicolon breakout, a
        # complete destructive statement, a trailing SQL comment, or a
        # destructive root wipe. That still rejects the classic payloads
        # ("DROP TABLE memory_records; -- …", "'; DROP TABLE users; --") while
        # letting prose about the same commands through.
        #
        # Trade-off recorded deliberately: a bare `| bash` in prose is no longer
        # flagged. Memora stores text; it does not execute it, so shell text in
        # a memory is inert. The patterns that matter for a memory store are the
        # prompt-injection ones above, which are unchanged.
        (
            r"""
            (?:
              ['";]\s*(?:DROP|TRUNCATE)\s+TABLE             # quote/semicolon breakout
              | (?:DROP|TRUNCATE)\s+TABLE\s+[\w.`"']+\s*;    # complete destructive statement
              | (?:DROP|TRUNCATE)\s+TABLE\b[^\n]*--          # destructive op + SQL comment
              | ;\s*rm\s+-rf\s+/(?:\s|$)                     # destructive root wipe
              | &&\s*rm\s+-rf\s+/(?:\s|$)
            )
            """,
            "Command / SQL Injection Vector",
            re.VERBOSE,
        ),
    ]

    # Patterns are (regex, label) or (regex, label, extra_flags). The extra-flags
    # form exists because the SQL/command pattern below is written in verbose
    # form with inline comments; compiling it without re.VERBOSE treats those
    # comments as literal text and the pattern then matches nothing at all.
    _COMPILED_PATTERNS = [
        (re.compile(entry[0], re.IGNORECASE | (entry[2] if len(entry) > 2 else 0)), entry[1])
        for entry in POISON_PATTERNS
    ]

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
