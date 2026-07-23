# How I Built an AI Agent That Queries 12 Microservices for $0.005

An enterprise intelligence agent that answers questions about services, APIs, databases, and dependencies — using a knowledge graph, semantic search, and live tools.

---

## The Problem

If you work on a platform with 10–50 microservices, you've wasted hours on questions like:

**"Who owns this service?"** — Slack someone, wait 2 hours for a reply.

**"What calls this endpoint?"** — Grep across 30 repos, read outdated Swagger specs.

**"What table stores payment schedules?"** — Ask the DBA, check the schema wiki (last updated 2022).

**"Is it safe to deprecate /refunds?"** — Tribal knowledge, 3 meetings, still not sure.

I decided to build a single AI agent that could answer all of these in seconds.

---

## What It Does

Ask in natural language, get answers backed by evidence:

**"Who owns the payment gateway?"**
→ Payments team. Instant graph lookup. 3 seconds, $0.005.

**"What's the impact of removing /refunds from Payment Gateway?"**
→ 3 consumers affected (Order Service, API Gateway, Reporting Service). Risk: HIGH. Recommendation: create a migration plan with deprecation timeline.

**"Show me the latest 5 orders"**
→ Live database query via Vault dynamic credentials. 5 seconds.

**"Any recent bugs with Search Service?"**
→ Live Jira query. 4 tickets returned with status and assignee.

A single chat interface backed by a knowledge graph, semantic search, live databases, live APIs, and issue trackers — with the AI reasoning across all of them.

---

## Architecture Overview

The system has four layers:

**1. Knowledge Graph (Neo4j)** — services, teams, dependencies, API endpoints. For instant lookups and dependency tracing.

**2. Semantic Search (pgvector)** — Swagger specs, Postman collections, Confluence docs, DB schemas. All chunked and embedded for concept matching.

**3. Live Tools** — Real-time DB queries via Vault, API calls with auto-OAuth2 retry, Jira search. For runtime state questions.

**4. MCP Servers** — GitHub code search, Atlassian integration. Extensible via the Model Context Protocol.

The AI agent orchestrates across all four, deciding which tools to call based on the question.

---

## Design Decision #1: Model Routing (80% Cost Savings)

Not every question needs GPT-4o. "Who owns X?" is a simple graph lookup — GPT-4o-mini handles it perfectly at one-tenth the cost.

I pattern-match on the question to route:

Simple patterns like "who owns", "list services", "what language" → **GPT-4o-mini (~$0.005/query)**

Everything else (impact analysis, multi-step reasoning, live queries) → **GPT-4o (~$0.03/query)**

Result: 70% of queries route to the fast model. Average cost dropped from $0.03 to $0.008 per query.

---

## Design Decision #2: The Meta-Tool Pattern (15K Tokens Saved)

MCP servers expose tools. GitHub alone has 41 tools. If you inject all tool schemas into every LLM call, that's ~15,000 tokens of schema definitions per request. At scale, that's $2–5/day burned on schema injection alone.

My solution: **one meta-tool** that routes to any MCP tool by name.

Instead of sending 41 individual tool schemas (~15K tokens), I send one tool definition (~500 tokens) that knows about all available sub-tools. The LLM picks the right sub-tool by name, and the meta-tool routes it to the correct MCP server.

Same capability. 30x fewer tokens.

---

## Design Decision #3: Tool Result Truncation

A single database query can return 50 rows with 30 columns — 10,000+ tokens of raw JSON. The LLM doesn't need all of it to answer the question.

Every tool result gets smart-truncated to 2KB before the LLM sees it:

- Database results: keep first 10 rows, add "showing 10 of 847"
- Search results: keep first 3, truncate content to 300 chars each
- API responses: keep first 10 items of arrays

The full result is logged for debugging. The LLM only sees what it needs. This prevents the #1 cause of budget blow-ups in agent loops.

---

## Design Decision #4: 3-Layer Secret Redaction

When you give an AI agent access to live databases and APIs, secrets WILL appear in tool results. Connection strings, tokens, API keys — they show up in DB columns, API headers, and error messages.

Three layers of defense:

**Layer 1:** Tool results are scrubbed before the LLM sees them (prevents the model from learning secrets).

**Layer 2:** Tool results are scrubbed before logging (prevents secrets in observability data).

**Layer 3:** The final answer is scrubbed before the user sees it (last line of defense).

Patterns caught: API keys (sk-...), Bearer tokens, GitHub PATs (ghp_...), AWS keys (AKIA...), database URLs, private keys, and passwords.

Plus: sensitive DB columns (password, token, secret, api_key) are auto-redacted at the query level before results even reach the redaction layer.

---

## Design Decision #5: Hybrid Search (Vector + Text)

Pure semantic search fails on exact matches. Ask for "Auth Service" and vector search might return "Identity Provider Manager" because they're semantically similar — but you wanted the exact service.

Solution: try vector search first, fall back to multi-stage text search.

**Stage 1:** Vector cosine similarity (semantic — finds conceptual matches like "payment flow" → "transaction_ledger")

**Stage 2 (if empty):** Exact phrase ILIKE match

**Stage 3 (if empty):** AND keywords (all words must match)

**Stage 4 (if empty):** OR keywords ranked by match count

This catches both "how does payment processing work?" (semantic) and "Auth Service endpoints" (exact).

---

## Design Decision #6: Vault Dynamic Credentials

Static database passwords in .env files are a security nightmare. They never rotate, they get shared, they end up in git history.

Instead: every single DB query fetches short-lived credentials from HashiCorp Vault at runtime. The credentials auto-expire. The connection is enforced read-only at the PostgreSQL transaction level.

The agent can never write to production. Credentials are never stored. No rotation needed.

---

## Design Decision #7: Token Budget Cap

Without a hard limit, an agent loop can spiral: tool returns partial results → LLM tries again → partial results → retry → repeat until you've burned $50 on one question.

Hard limits: maximum 5 tool-call rounds, maximum 30,000 tokens per query. If either is hit, the agent is forced to summarize what it has and stop.

In practice, 95% of queries resolve in 1–2 tool calls. The cap is insurance against the 1% that would otherwise cost $10+.

---

## Results

Simple queries (ownership, listing): **~3 seconds, ~$0.005**

Complex queries (impact analysis, multi-tool): **~8 seconds, ~$0.03**

Live database queries (via Vault): **~5 seconds** including credential fetch

Model routing savings: **~80% cost reduction** on simple queries

Meta-tool savings: **~15K tokens/request** compared to injecting all MCP schemas

---

## What I'd Do Differently

**Add evaluation metrics.** I track cost and latency, but not answer accuracy. Next step: a golden set of 50 questions with expected answers, run weekly.

**Streaming responses.** Currently waits for the full agent loop to finish. SSE streaming would show tool calls in real-time, which feels much faster.

**Cache frequent queries.** "Who owns X?" gets asked 10 times/day with the same answer. A 5-minute cache on graph queries would cut costs further.

---

## Try It Yourself

The project is open source: https://github.com/srikanthanugu892/nexus-ai

It ships with 12 sample microservices so you can try it without connecting to real infrastructure. Clone, add your OpenAI key, docker compose up, and start asking questions.

---

*If you're building AI agents for enterprise use cases, I'd love to hear what patterns you've found useful. The tradeoffs between cost, accuracy, and latency are endlessly interesting.*
