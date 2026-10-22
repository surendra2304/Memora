from __future__ import annotations
from dataclasses import dataclass
import hashlib
import hmac
import re
from typing import Iterable


SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*[^\s,;]+"),
)


@dataclass(frozen=True, slots=True)
class SecretFinding:
    pattern: str
    start: int
    end: int


class SecretScanner:
    def __init__(self, patterns: Iterable[re.Pattern[str]] = SECRET_PATTERNS):
        self.patterns = tuple(patterns)

    def scan(self, text: str) -> list[SecretFinding]:
        findings: list[SecretFinding] = []
        for pattern in self.patterns:
            for match in pattern.finditer(text):
                findings.append(SecretFinding(pattern.pattern, match.start(), match.end()))
        return sorted(findings, key=lambda x: (x.start, x.end))

    def contains_secret(self, text: str) -> bool:
        return bool(self.scan(text))


class SecretRedactor:
    def __init__(self, replacement: str = "[REDACTED]"):
        self.replacement = replacement

    def redact(self, text: str) -> str:
        result = text
        for pattern in SECRET_PATTERNS:
            result = pattern.sub(self.replacement, result)
        return result


def keyed_digest(value: str, tenant_secret: str) -> str:
    return hmac.new(tenant_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())
