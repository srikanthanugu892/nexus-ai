"""Confluence Documentation Collector — indexes pages from SERENITY space into pgvector.

Uses the Confluence REST API to fetch pages, converts content to plain text,
chunks into searchable segments, and stores in pgvector.

Usage:
    python -m nexus_ai.collectors.confluence
"""

import asyncio
import json
import re
import time

import httpx

from nexus_ai.config import settings
from nexus_ai.db.postgres import get_pg_pool, close_pg

# Confluence API config
BASE_URL = f"{settings.atlassian_url}/wiki/rest/api"
AUTH = (settings.atlassian_email, settings.atlassian_api_token)
SPACE_KEY = settings.confluence_space

# Key pages to always index (by ID)
SEED_PAGE_IDS = [
    "2302198495",  # Core Platform Services
]

# Chunk settings
MAX_CHUNK_SIZE = 500  # tokens (~2000 chars)
CHUNK_OVERLAP = 100   # tokens (~400 chars)
MAX_CHARS_PER_CHUNK = 2000
OVERLAP_CHARS = 400


def clean_confluence_content(raw_content: str) -> str:
    """Convert Confluence content (markdown/HTML) to clean searchable text."""
    text = raw_content

    # Remove HTML tags if any remain
    text = re.sub(r'<[^>]+>', ' ', text)
    # Remove markdown link syntax but keep text: [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove image references
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    # Remove excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    # Remove horizontal rules
    text = re.sub(r'[-=]{3,}', '', text)

    return text.strip()


def chunk_text(text: str, title: str) -> list[str]:
    """Split text into overlapping chunks for embedding.

    Each chunk gets the page title prepended for context.
    """
    if len(text) <= MAX_CHARS_PER_CHUNK:
        return [f"Page: {title}\n\n{text}"]

    chunks = []
    start = 0
    while start < len(text):
        end = start + MAX_CHARS_PER_CHUNK

        # Try to break at a paragraph or sentence boundary
        if end < len(text):
            # Look for paragraph break near the end
            para_break = text.rfind('\n\n', start + MAX_CHARS_PER_CHUNK // 2, end)
            if para_break > start:
                end = para_break
            else:
                # Look for sentence break
                sent_break = text.rfind('. ', start + MAX_CHARS_PER_CHUNK // 2, end)
                if sent_break > start:
                    end = sent_break + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(f"Page: {title}\n\n{chunk}")

        # Move forward with overlap
        start = end - OVERLAP_CHARS
        if start <= 0 and end >= len(text):
            break

    return chunks


async def fetch_space_pages(client: httpx.AsyncClient, limit: int = 50) -> list[dict]:
    """Fetch pages from the SERENITY space via CQL search."""
    pages = []
    start = 0

    while True:
        try:
            resp = await client.get(
                f"{BASE_URL}/content/search",
                params={
                    "cql": f'space="{SPACE_KEY}" AND type=page',
                    "limit": min(limit - len(pages), 25),
                    "start": start,
                    "expand": "body.view,version",
                },
                auth=AUTH,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

            results = data.get("results", [])
            if not results:
                break

            for page in results:
                body = page.get("body", {}).get("view", {}).get("value", "")
                pages.append({
                    "id": page["id"],
                    "title": page["title"],
                    "content": body,
                    "url": f"{settings.atlassian_url}/wiki/spaces/{SPACE_KEY}/pages/{page['id']}",
                    "version": page.get("version", {}).get("number", 0),
                })

            if len(pages) >= limit:
                break

            # Check for more pages
            next_link = data.get("_links", {}).get("next")
            if not next_link:
                break
            start += len(results)

        except httpx.HTTPStatusError as e:
            print(f"  ✗ HTTP {e.response.status_code} fetching space pages (start={start})")
            break
        except Exception as e:
            print(f"  ✗ Error fetching space pages: {e}")
            break

    return pages


async def fetch_page_by_id(client: httpx.AsyncClient, page_id: str) -> dict | None:
    """Fetch a single page by ID with full content."""
    try:
        resp = await client.get(
            f"{BASE_URL}/content/{page_id}",
            params={"expand": "body.view,version,space"},
            auth=AUTH,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        body = data.get("body", {}).get("view", {}).get("value", "")
        return {
            "id": data["id"],
            "title": data["title"],
            "content": body,
            "url": f"{settings.atlassian_url}/wiki/spaces/{SPACE_KEY}/pages/{data['id']}",
            "version": data.get("version", {}).get("number", 0),
        }
    except Exception as e:
        print(f"  ✗ Error fetching page {page_id}: {e}")
        return None


async def store_chunks(chunks: list[str], page: dict):
    """Store text chunks in pgvector."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        for chunk in chunks:
            metadata = {
                "page_id": page["id"],
                "page_title": page["title"],
                "version": page["version"],
            }
            await conn.execute(
                """
                INSERT INTO embeddings (content, source_type, source_url, service_name, metadata)
                VALUES ($1, 'confluence', $2, $3, $4)
                """,
                chunk,
                page["url"],
                None,  # service_name is null for general docs
                json.dumps(metadata),
            )


async def run_collector(max_pages: int = 50) -> dict:
    """Run the full Confluence collector pipeline."""
    print("=" * 60)
    print("Confluence Documentation Collector")
    print(f"Space: {SPACE_KEY} | Max pages: {max_pages}")
    print("=" * 60)

    if not settings.atlassian_email or not settings.atlassian_api_token:
        print("\n✗ ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN must be set in .env")
        return {"error": "Missing Confluence credentials"}

    start_time = time.time()
    total_chunks = 0
    pages_indexed = 0

    # Clear old Confluence entries to avoid duplicates on re-run
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        deleted = await conn.execute("DELETE FROM embeddings WHERE source_type = 'confluence'")
        print(f"\n  Cleared previous Confluence entries")

    async with httpx.AsyncClient() as client:
        # 1. Fetch seed pages by ID (always index these)
        print(f"\nFetching {len(SEED_PAGE_IDS)} seed pages...")
        for page_id in SEED_PAGE_IDS:
            page = await fetch_page_by_id(client, page_id)
            if page:
                text = clean_confluence_content(page["content"])
                chunks = chunk_text(text, page["title"])
                await store_chunks(chunks, page)
                total_chunks += len(chunks)
                pages_indexed += 1
                print(f"  ✓ {page['title']} ({len(chunks)} chunks)")

        # 2. Fetch recent pages from the space
        print(f"\nFetching up to {max_pages} pages from {SPACE_KEY} space...")
        pages = await fetch_space_pages(client, limit=max_pages)
        print(f"  Found {len(pages)} pages")

        seen_ids = set(SEED_PAGE_IDS)
        for page in pages:
            # Skip already-indexed pages (seed pages or duplicates)
            if page["id"] in seen_ids:
                continue
            seen_ids.add(page["id"])

            text = clean_confluence_content(page["content"])
            if len(text) < 50:  # Skip nearly-empty pages
                continue

            chunks = chunk_text(text, page["title"])
            await store_chunks(chunks, page)
            total_chunks += len(chunks)
            pages_indexed += 1
            print(f"  ✓ {page['title']} ({len(chunks)} chunks)")

    duration = time.time() - start_time

    summary = {
        "pages_indexed": pages_indexed,
        "total_chunks": total_chunks,
        "space": SPACE_KEY,
        "duration_seconds": round(duration, 1),
    }

    print(f"\n{'=' * 60}")
    print(f"Done in {summary['duration_seconds']}s")
    print(f"  Pages indexed: {pages_indexed}")
    print(f"  Total chunks stored: {total_chunks}")
    print(f"{'=' * 60}")

    return summary


async def main():
    try:
        await run_collector(max_pages=50)
    finally:
        await close_pg()


if __name__ == "__main__":
    asyncio.run(main())
