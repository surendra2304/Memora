"""
Secret Scanner and Credential Detection for Memora Write Pipeline
Scans incoming memory content for API keys, bearer tokens, private keys, and passwords.
"""
import re
from typing import List, Tuple

class SecretDetectedSecurityViolation(Exception):
    def __init__(self, secret_types: List[str]):
        self.secret_types = secret_types
        super().__init__(f"Security Violation: Content contains unmasked secrets/credentials: {', '.join(secret_types)}")

class SecretScanner:
    SECRET_PATTERNS: List[Tuple[str, re.Pattern]] = [
        ("Google API Key", re.compile(r"AIza[0-9A-Za-z-_]{30,40}")),
        ("OpenAI API Key", re.compile(r"sk-[a-zA-Z0-9_-]{20,}")),
        ("GitHub Personal Access Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}")),
        ("GitHub Fine-Grained Token", re.compile(r"github_pat_[A-Za-z0-9_]{60,}")),
        ("Private Key Block", re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH|PGP)? ?PRIVATE KEY-----")),
        ("JWT Token", re.compile(r"eyJ[A-Za-z0-9-_=]+\.eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]{10,}")),
        ("Hardcoded Password", re.compile(r"(?:password|passwd|pwd|secret_key)\s*[:=]\s*['\"][^\s'\"]{6,}['\"]", re.IGNORECASE)),
        ("Bearer Token", re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{25,}", re.IGNORECASE)),
        ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ]

    @classmethod
    def scan_content(cls, content: str) -> List[str]:
        flagged = []
        for name, pattern in cls.SECRET_PATTERNS:
            if pattern.search(content):
                flagged.append(name)
        return flagged

    @classmethod
    def validate_content_safety(cls, content: str) -> None:
        detected = cls.scan_content(content)
        if detected:
            raise SecretDetectedSecurityViolation(detected)