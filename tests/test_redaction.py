"""Tests for secret redaction — ensures no credentials leak to users."""

from nexus_ai.agent.redaction import redact_secrets


def test_redacts_api_key():
    text = "The API key is sk-abc123def456ghi789jkl012mno"
    assert "sk-abc123" not in redact_secrets(text)
    assert "[REDACTED_API_KEY]" in redact_secrets(text)


def test_redacts_bearer_token():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test"
    assert "eyJhbGciOi" not in redact_secrets(text)
    assert "Bearer [REDACTED]" in redact_secrets(text)


def test_redacts_github_pat():
    text = "Token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklm"
    assert "ghp_ABCDEF" not in redact_secrets(text)
    assert "[REDACTED_GITHUB_PAT]" in redact_secrets(text)


def test_redacts_aws_key():
    text = "Access key: AKIAIOSFODNN7EXAMPLE"
    assert "AKIAIOSFODNN7EXAMPLE" not in redact_secrets(text)
    assert "[REDACTED_AWS_KEY]" in redact_secrets(text)


def test_preserves_safe_text():
    text = "The Order Service is owned by the Commerce team."
    assert redact_secrets(text) == text


def test_redacts_postgres_url():
    text = "Connected to postgresql://admin:s3cret@db.internal.example.com:5432/orders"
    assert "s3cret" not in redact_secrets(text)
    assert "[REDACTED_DB_URL]" in redact_secrets(text)
