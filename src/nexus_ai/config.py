"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # API auth
    api_key: str = "nexus-dev-key-change-me"

    # LLM (OpenAI-compatible endpoint — works with LiteLLM, Ollama, vLLM, OpenAI, etc.)
    litellm_endpoint: str = "http://localhost:4000"
    litellm_api_key: str = ""
    llm_model: str = "gpt-4o"
    llm_model_fast: str = "gpt-4o-mini"  # Cheaper model for simple queries

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "nexus-dev-password"

    # PostgreSQL + pgvector
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "nexus_ai"
    postgres_user: str = "nexus"
    postgres_password: str = "nexus-dev-password"

    # GitHub (github.com or GitHub Enterprise)
    github_host: str = "github.com"
    github_token: str = ""
    github_org: str = ""

    # Jira / Confluence (Atlassian Cloud)
    atlassian_url: str = "https://your-org.atlassian.net"
    atlassian_email: str = ""
    atlassian_api_token: str = ""
    confluence_space: str = ""

    # Vault (HashiCorp — for dynamic DB credentials and service-to-service auth)
    vault_host: str = "vault.example.com"
    vault_token: str = ""
    vault_env: str = "dev"
    # TLS verification for Vault/internal services.
    # Defaults to True (secure). Set to "false" only if internal services use self-signed certs.
    # Set to a CA bundle path (e.g., /etc/ssl/certs/internal-ca.pem) for custom CA.
    vault_tls_verify: str = "true"

    # OAuth2 (for service-to-service authentication — client_secret fetched from Vault at runtime)
    auth0_issuer: str = "https://your-tenant.auth0.com"
    auth0_client_id: str = ""

    @property
    def tls_verify(self) -> bool | str:
        """Return TLS verify setting: True (default), False, or path to CA bundle."""
        if not self.vault_tls_verify:
            return True  # Default secure
        if self.vault_tls_verify.lower() in ("false", "0", "no"):
            return False
        if self.vault_tls_verify.lower() in ("true", "1", "yes"):
            return True
        # Assume it's a file path to a CA bundle
        return self.vault_tls_verify

    @property
    def postgres_dsn(self) -> str:
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def postgres_async_dsn(self) -> str:
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
