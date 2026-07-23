-- Nexus AI: Initial schema — pgvector embeddings + interaction logs

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Embeddings table for semantic search across all data sources
CREATE TABLE IF NOT EXISTS embeddings (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(1536),
    source_type VARCHAR(50) NOT NULL,  -- 'swagger', 'postman', 'bruno', 'confluence', 'jira', 'db_schema', 'seed'
    source_url TEXT,
    service_name VARCHAR(100),
    metadata JSONB DEFAULT '{}',
    last_updated TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Index for cosine similarity search
CREATE INDEX IF NOT EXISTS idx_embeddings_cosine
    ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Index for filtering by source type and service
CREATE INDEX IF NOT EXISTS idx_embeddings_source_type ON embeddings (source_type);
CREATE INDEX IF NOT EXISTS idx_embeddings_service_name ON embeddings (service_name);

-- Interaction logs for observability and prompt tuning
CREATE TABLE IF NOT EXISTS interaction_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT NOW(),
    question TEXT NOT NULL,
    tool_calls JSONB DEFAULT '[]',
    final_answer TEXT,
    model VARCHAR(100),
    total_tokens INTEGER,
    duration_ms INTEGER,
    error TEXT,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_interaction_logs_timestamp ON interaction_logs (timestamp DESC);
