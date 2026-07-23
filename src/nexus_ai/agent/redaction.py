"""Output redaction — scrub secrets from agent responses before returning to users.

Three-layer defense:
1. Tool results → before sending to LLM
2. Tool results → before logging
3. Final answer → before returning to user
"""

import re

# Patterns that should never appear in agent output
SECRET_PATTERNS = [
    (re.compile(r'sk-[a-zA-Z0-9_]{20,}'), '[REDACTED_API_KEY]'),
    (re.compile(r'Bearer\s+[a-zA-Z0-9._\-]{20,}'), 'Bearer [REDACTED]'),
    (re.compile(r'ghp_[a-zA-Z0-9]{36,}'), '[REDACTED_GITHUB_PAT]'),
    (re.compile(r'xox[baprs]-[a-zA-Z0-9\-]{10,}'), '[REDACTED_SLACK_TOKEN]'),
    (re.compile(r'password["\s:=]+["\']?[^\s"\']{8,}', re.IGNORECASE), 'password: [REDACTED]'),
    (re.compile(r'token["\s:=]+["\']?[a-zA-Z0-9._\-]{20,}', re.IGNORECASE), 'token: [REDACTED]'),
    (re.compile(r'AKIA[0-9A-Z]{16}'), '[REDACTED_AWS_KEY]'),
    (re.compile(r'-----BEGIN (RSA |EC )?PRIVATE KEY-----'), '[REDACTED_PRIVATE_KEY]'),
    (re.compile(r'postgresql://[^\s"]+'), '[REDACTED_DB_URL]'),
]


def redact_secrets(text: str) -> str:
    """Remove any secret patterns from text before returning to users."""
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
