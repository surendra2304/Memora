"""
Regression tests for the secret scanner's unquoted-credential gap.

The "Hardcoded Password" pattern required the value to be quoted:

    (?:password|passwd|pwd|secret_key)\\s*[:=]\\s*['\"][^\\s'\"]{6,}['\"]

so every unquoted form passed straight through the write pipeline. Verified
before the fix, scan_content returned [] for:

    db password=hunter2secret
    password: hunter2secret
    export DB_PASSWORD=SuperSecret123
    api_key = 9f8a7b6c5d4e3f2a1b0c
    AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
"""
import hashlib

import pytest

from core.memory.pipeline.secret_scanner import (
    SecretDetectedSecurityViolation,
    SecretScanner,
)

#: Real-looking credentials in unquoted form. Every one of these was missed.
UNQUOTED_SECRETS = [
    "db password=hunter2secret",
    "password: hunter2secret",
    "export DB_PASSWORD=SuperSecret123",
    "api_key = 9f8a7b6c5d4e3f2a1b0c",
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "client_secret: 8Kx2mQ9vLp4nR7tY1wZa",
    "access_token=1/AbCdEfGhIjKlMnOpQrStUvWxYz",
    "auth-token: 9a8b7c6d5e4f3a2b1c0d9e8f",
]

#: Already covered before the fix; must keep working.
QUOTED_AND_TOKEN_SECRETS = [
    'password="hunter2secret"',
    "sk-abcdefghijklmnopqrstuvwxyz0123456789",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
]

#: Must NOT be flagged. Placeholders, prose, and digests this codebase stores.
BENIGN = [
    "nothing sensitive here at all",
    "password=<your-password-here>",
    "password=${DB_PASSWORD}",
    "password: changeme",
    "password = xxxx",
    "password: redacted",
    "api_key=YOUR_API_KEY",
    "set the password to something strong",
    "the password field must be at least 12 characters long",
    "Rotate the API key every 90 days per policy",
    "Password management is handled by the vault",
    "We never store passwords in plaintext",
]


@pytest.mark.parametrize("content", UNQUOTED_SECRETS)
def test_unquoted_credentials_are_detected(content):
    """The actual gap: unquoted assignments must not pass."""
    assert SecretScanner.scan_content(content), f"missed a real credential: {content!r}"


@pytest.mark.parametrize("content", UNQUOTED_SECRETS)
def test_unquoted_credentials_raise(content):
    with pytest.raises(SecretDetectedSecurityViolation):
        SecretScanner.validate_content_safety(content)


@pytest.mark.parametrize("content", QUOTED_AND_TOKEN_SECRETS)
def test_previously_detected_secrets_still_are(content):
    """No regression in the patterns that already worked."""
    assert SecretScanner.scan_content(content), f"regression, no longer detected: {content!r}"


@pytest.mark.parametrize("content", BENIGN)
def test_benign_content_is_not_flagged(content):
    """A scanner that cries wolf gets disabled, so precision matters."""
    assert SecretScanner.scan_content(content) == [], f"false positive on: {content!r}"


@pytest.mark.parametrize(
    "digest",
    [
        hashlib.sha1(b"memora").hexdigest(),
        hashlib.sha1(b"xenon compressor").hexdigest(),
        hashlib.sha256(b"memora").hexdigest(),
    ],
)
def test_hex_digests_are_not_mistaken_for_credentials(digest):
    """This codebase stores content_sha256 and logs digests constantly.

    A bare 40-character base64/hex pattern was tried and rejected for exactly
    this reason: it flagged every SHA-1 digest.
    """
    assert SecretScanner.scan_content(digest) == []
    assert SecretScanner.scan_content(f"the build checksum is {digest}") == []


def test_a_secret_inside_a_longer_memory_is_still_caught():
    content = (
        "Notes from the xenon retro:\n"
        "  - the compressor seal needs a torque check\n"
        "  - staging DB_PASSWORD=SuperSecret123 (rotate this!)\n"
        "  - follow up with FORGE\n"
    )
    assert "Unquoted Credential Assignment" in SecretScanner.scan_content(content)
