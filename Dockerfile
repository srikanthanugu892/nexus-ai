FROM python:3.11-slim

WORKDIR /app

# Install system deps for asyncpg and MCP servers
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (needed to spawn GitHub MCP server via docker socket)
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker

# Install uv (needed for uvx to run MCP servers like mcp-atlassian)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Copy source and install
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

# Copy remaining files
COPY data/ data/
COPY migrations/ migrations/

EXPOSE 8000

CMD ["uvicorn", "nexus_ai.main:app", "--host", "0.0.0.0", "--port", "8000"]
