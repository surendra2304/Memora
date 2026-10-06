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

# Alias for backwards compatibility
SecretLeakageError = SecretDetectedSecurityViolation

class SecretScanner:
    SECRET_PATTERNS: List[Tuple[str, re.Pattern]] = [
        ("Google API Key", re.compile(r"AIza[0-9A-Za-z-_]{30,40}")),
        ("OpenAI API Key", re.compile(r"sk-[a-zA-Z0-9_-]{20,}")),
        ("GitHub Personal Access Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}")),
        ("GitHub Fine-Grained Token", re.compile(r"github_pat_[A-Za-z0-9_]{60,}")),
        ("Private Key Block", re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH|PGP)? ?PRIVATE KEY-----")),
        ("JWT Token", re.compile(r"eyJ[A-Za-z0-9-_=]+\.eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_.+/=]{10,}")),
        ("Hardcoded Password", re.compile(r"(?:password|passwd|pwd|secret_key)\s*[:=]\s*['\"][^\s'\"]{6,}['\"]", re.IGNORECASE)),
        # Unquoted credential assignments. The pattern above requires quotes, so
        # `password=hunter2secret`, `DB_PASSWORD=SuperSecret123` and
        # `api_key = 9f8a7b...` all passed straight through. The value must look
        # like a credential (no whitespace, 6+ chars) and must not be an obvious
        # placeholder.
        #
        # Deliberately NOT covered: a bare 40-character base64/hex string. That
        # also matches every SHA-1 digest, which this codebase stores and logs,
        # so the false-positive rate was unacceptable.
        (
            "Unquoted Credential Assignment",
            re.compile(
                r"""
                (?:password|passwd|pwd|secret|secret[_-]?key|api[_-]?key|apikey|
                   access[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|
                   private[_-]?key)
                \s*[:=]\s*
                (?!\s*                                  # reject an empty value
                   (?:<[^>]*>|\$\{[^}]*\}|              # <placeholder> or ${ENV_VAR}
                      x{3,}|\*{3,}|\.{3,}|              # xxxx, ****, ...
                      [A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)+| # YOUR_API_KEY, DB_PASSWORD
                      (?:your|my|the|example|sample|dummy|fake|test|changeme|
                         replace[_-]?me|redacted|none|null|todo|tbd)\b)
                )
                [^\s'"`,;)]{6,}
                """,
                re.IGNORECASE | re.VERBOSE,
            ),
        ),
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