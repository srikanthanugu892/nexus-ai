# How I Built an AI Agent That Queries 12 Microservices for $0.005

*An enterprise intelligence agent that answers questions about services, APIs, databases, and dependencies — using a knowledge graph, semantic search, and live tools.*

---

## The Problem

If you work on a platform with 10–50 microservices, you've wasted hours on questions like:

- **"Who owns this service?"** → Slack someone, wait 2 hours for a reply
- **"What calls this endpoint?"** → Grep across 30 repos, read outdated Swagger specs
- **"What table stores payment schedules?"** → Ask the DBA, check the schema wiki (last updated 2022)
- **"Is it safe to deprecate /refunds?"** → Tribal knowledge, 3 meetings, still not sure

I decided to build a single AI agent that could answer all of these in seconds.

---

## What It Does

```
> "Who owns the payment gateway?"
→ Payments team (instant graph lookup, 3s, ~$0.005)

> "What's the impact of removing /refunds from Payment Gateway?"
→ 3 consumers affected. Risk: HIGH. Recommendation: cross-team migration plan.

> "Show me the latest 5 orders"
→ [live DB query via Vault dynamic credentials, 5s]

> "Any recent bugs with Search Service?"
→ [live Jira query, 4 tickets returned]
```

A single chat interface where you ask in natural language, and the agent reasons across a knowledge graph, semantic search index, live databases, live APIs, and issue trackers.

---

## Architecture Overview

The system has four layers:

1. **Knowledge Graph** (Neo4j) — services, teams, dependencies, API endpoints
2. **Semantic Search** (pgvector) — Swagger specs, Postman collections, Confluence docs, DB schemas
3. **Live Tools** — real-time DB queries via Vault, API calls with auto-OAuth2, Jira search
4. **MCP Servers** — GitHub code search, Atlassian (extensible)

The AI agent orchestrates across all four, deciding which tools to call based on the question.

---

## Design Decision #1: Model Routing (80% Cost Savings)

Not every question needs GPT-4o. "Who owns X?" is a simple graph lookup — GPT-4o-mini handles it perfectly at 1/10th the cost.

```python
SIMPLE_PATTERNS = ["who owns", "list services", "what language", "find service"]

def select_model(question: str) -> str:
    for pattern in SIMPLE_PATTERNS:
        if pattern in question.lower():
            return "gpt-4o-mini"   # ~$0.005/query
    return "gpt-4o"               # ~$0.03/query
```

**Result:** 70% of queries route to the fast model. Average cost dropped from $0.03 to $0.008/query.

---

## Design Decision #2: The Meta-Tool Pattern (15K Tokens Saved Per Request)

MCP (Model Context Protocol) servers expose tools — GitHub alone has 41 tools. If you inject all tool schemas into every LLM call, that's ~15,000 tokens of schema definitions. At scale, that's $2-5/day just on schema injection.

My solution: **one meta-tool** that can route to any MCP tool by name.

```python
# Instead of 41 individual tool schemas (~15K tokens):
{
    "name": "call_mcp_tool",
    "description": "Call any MCP tool by name. Available: search_code, get_file_contents, ...",
    "parameters": {
        "tool_name": {"type": "string"},
        "arguments": {"type": "object"}
    }
}
# = ~500 tokens. Same capability, 30x cheaper.
```

The LLM sees one tool definition, picks the right sub-tool by name, and the meta-tool routes it to the correct MCP server.

---

## Design Decision #3: Tool Result Truncation

A single database query can return 50 rows with 30 columns each — 10,000+ tokens of raw JSON. The LLM doesn't need all of it to answer the question.

```python
def truncate_tool_result(result: str, max_chars: int = 2000) -> str:
    """Smart truncation that preserves structure."""
    data = json.loads(result)
    
    # DB results: keep first 10 rows
    if "rows" in data and len(data["rows"]) > 10:
        data["rows"] = data["rows"][:10]
        data["truncated"] = True
        data["showing"] = f"10 of {original_count}"
    
    return json.dumps(data)
```

The full result is logged for debugging, but the LLM only sees what it needs. Prevents the #1 cause of budget blow-ups in agent loops.

---

## Design Decision #4: 3-Layer Secret Redaction

When you give an AI agent access to live databases and APIs, secrets WILL appear in tool results. Connection strings, tokens, API keys — they show up in DB columns, API headers, and error messages.

Three layers of defense:

1. **Tool results → before LLM sees them** (prevents the model from learning secrets)
2. **Tool results → before logging** (prevents secrets in observability data)
3. **Final answer → before user sees it** (last line of defense)

```python
SECRET_PATTERNS = [
    (re.compile(r'sk-[a-zA-Z0-9_]{20,}'), '[REDACTED_API_KEY]'),
    (re.compile(r'Bearer\s+[a-zA-Z0-9._\-]{20,}'), 'Bearer [REDACTED]'),
    (re.compile(r'postgresql://[^\s"]+'), '[REDACTED_DB_URL]'),
    # ... 9 patterns total
]
```

Plus: sensitive DB columns (`password`, `token`, `secret`, `api_key`) are auto-redacted at the query level before results even reach the redaction layer.

---

## Design Decision #5: Hybrid Search (Vector + Text)

Pure semantic search fails on exact matches. Ask for "Auth Service" and vector search might return "Identity Provider Manager" because they're semantically similar — but you wanted the exact service.

Solution: try vector search first, fall back to text:

```python
async def search_documentation(query: str) -> str:
    # 1. Vector search (semantic — finds conceptual matches)
    rows = await vector_search(query_embedding, limit=15)
    
    # 2. If nothing found: multi-stage text fallback
    if not rows:
        rows = await text_search_exact_phrase(query)      # "Auth Service"
    if not rows:
        rows = await text_search_and_keywords(query)      # config AND service
    if not rows:
        rows = await text_search_or_ranked(query)         # config OR service, ranked
    
    return results
```

This catches both "how does payment processing work?" (semantic) and "Auth Service endpoints" (exact).

---

## Design Decision #6: Vault Dynamic Credentials

Static database passwords in `.env` files are a security nightmare. They never rotate, they get shared, they end up in git history.

Instead: every DB query fetches short-lived credentials from HashiCorp Vault at runtime.

```python
# 1. Get temporary credentials (auto-expire after 720h)
creds = await vault.get(f"/database/creds/{db_role}")

# 2. Connect with read-only enforcement at transaction level
conn = await asyncpg.connect(
    host=db_host, database=database,
    user=creds["username"], password=creds["password"],
    server_settings={"default_transaction_read_only": "on"},
)

# 3. Credentials auto-expire — no rotation needed
```

The agent can never write to production, and credentials are never stored.

---

## Design Decision #7: Token Budget Cap

Without a hard limit, an agent loop can spiral: tool returns partial results → LLM tries again with different query → partial results → retry → repeat until you've burned $50 on one question.

```python
MAX_TOOL_ROUNDS = 5          # Hard stop after 5 tool calls
MAX_TOKENS_PER_QUERY = 30000  # Hard stop if token usage exceeds this

for round in range(MAX_TOOL_ROUNDS):
    if total_tokens > MAX_TOKENS_PER_QUERY:
        # Force a summary with no more tool calls
        break
```

In practice, 95% of queries resolve in 1-2 tool calls. The cap is insurance against the 1% that would otherwise cost $10+.

---

## Results

| Metric | Value |
|--------|-------|
| Simple query latency | ~3 seconds |
| Complex query latency | ~8 seconds |
| Simple query cost | ~$0.005 |
| Complex query cost | ~$0.03 |
| Model routing savings | ~80% |
| Token savings from meta-tool | ~15K tokens/request |

The agent handles ownership lookups, dependency tracing, live database queries, API calls, impact analysis, Jira search, documentation search, and GitHub code search — all through natural language.

---

## What I'd Do Differently

1. **Add evaluation metrics** — I track cost and latency, but not answer accuracy. Next step: a golden set of 50 questions with expected answers, run weekly.

2. **Streaming responses** — Currently waits for the full agent loop to finish. SSE streaming would show tool calls in real-time.

3. **Caching frequent queries** — "Who owns X?" is asked 10 times/day with the same answer. A 5-minute cache on graph queries would cut costs further.

---

## Try It Yourself

The project is open source: [github.com/srikanthanugu892/nexus-ai](https://github.com/srikanthanugu892/nexus-ai)

```bash
git clone https://github.com/srikanthanugu892/nexus-ai.git
cd nexus-ai
cp .env.example .env  # Add your OpenAI key
docker compose up --build -d
curl -X POST localhost:8000/admin/collectors/run-all
open http://localhost:3000
```

It ships with 12 sample microservices so you can try it without connecting to real infrastructure.

---

*If you're building AI agents for enterprise use cases, I'd love to hear what patterns you've found useful. The tradeoffs between cost, accuracy, and latency are endlessly interesting.*

---

**Tags:** AI, LLM, System Design, Software Engineering, MCP, Knowledge Graph, Enterprise
