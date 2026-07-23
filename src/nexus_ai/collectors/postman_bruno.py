"""Postman & Bruno Collection Collector — indexes API collections for search.

Fetches Postman (v2.1 JSON) and Bruno (.bru) collections from GitHub
repositories and indexes them as searchable chunks in pgvector.

Configure your collection repos in COLLECTION_REPOS below.
"""

import asyncio
import json
import re
from pathlib import Path

import httpx

from nexus_ai.config import settings
from nexus_ai.db.postgres import get_pg_pool

# Configure your API collection repositories here
COLLECTION_REPOS = {
    "postman": {
        "repo": "your-org/api-collections",
        "path": "postman",
        "format": "postman_v2",
    },
    "bruno": {
        "repo": "your-org/api-collections",
        "path": "bruno",
        "format": "bruno",
    },
}


async def fetch_github_dir(client: httpx.AsyncClient, repo: str, path: str) -> list[dict]:
    """Fetch directory listing from GitHub API."""
    api_base = f"https://{settings.github_host}/api/v3" if settings.github_host != "github.com" else "https://api.github.com"
    org = settings.github_org

    resp = await client.get(
        f"{api_base}/repos/{org}/{repo}/contents/{path}",
        headers={"Authorization": f"token {settings.github_token}"},
    )
    if resp.status_code != 200:
        return []
    return resp.json()


async def fetch_github_file(client: httpx.AsyncClient, repo: str, path: str) -> str:
    """Fetch raw file content from GitHub."""
    api_base = f"https://{settings.github_host}/api/v3" if settings.github_host != "github.com" else "https://api.github.com"
    org = settings.github_org

    resp = await client.get(
        f"{api_base}/repos/{org}/{repo}/contents/{path}",
        headers={
            "Authorization": f"token {settings.github_token}",
            "Accept": "application/vnd.github.v3.raw",
        },
    )
    if resp.status_code != 200:
        return ""
    return resp.text


def parse_postman_collection(content: str) -> list[dict]:
    """Parse a Postman v2.1 JSON collection into searchable chunks."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []

    collection_name = data.get("info", {}).get("name", "Unknown")
    chunks = []

    def extract_items(items, prefix=""):
        for item in items:
            if "item" in item:
                # Folder — recurse
                folder_name = item.get("name", "")
                extract_items(item["item"], prefix=f"{prefix}{folder_name}/")
            elif "request" in item:
                req = item["request"]
                method = req.get("method", "GET")
                url = req.get("url", {})
                if isinstance(url, str):
                    path = url
                else:
                    path = "/".join(url.get("path", []))

                name = item.get("name", "")
                body = ""
                if req.get("body", {}).get("raw"):
                    body = req["body"]["raw"][:200]

                chunk = f"{method} /{path} — {name}"
                if body:
                    chunk += f"\nBody: {body}"

                chunks.append({
                    "content": chunk,
                    "metadata": {
                        "collection": collection_name,
                        "method": method,
                        "path": f"/{path}",
                        "name": name,
                    }
                })

    extract_items(data.get("item", []))
    return chunks


def parse_bruno_file(content: str, filename: str) -> dict | None:
    """Parse a .bru file into a searchable chunk."""
    method_match = re.search(r'^(get|post|put|delete|patch)\s*\{', content, re.MULTILINE | re.IGNORECASE)
    url_match = re.search(r'url:\s*(.+)', content)
    name_match = re.search(r'name:\s*(.+)', content)

    if not url_match:
        return None

    method = method_match.group(1).upper() if method_match else "GET"
    url = url_match.group(1).strip()
    name = name_match.group(1).strip() if name_match else filename

    chunk_text = f"{method} {url} — {name}"

    # Try to extract body
    body_match = re.search(r'body:json\s*\{(.*?)\}', content, re.DOTALL)
    if body_match:
        chunk_text += f"\nBody: {body_match.group(1).strip()[:200]}"

    return {
        "content": chunk_text,
        "metadata": {"method": method, "url": url, "name": name, "source_file": filename},
    }


async def collect_postman_bruno() -> dict:
    """Collect and index Postman/Bruno API collections from GitHub."""
    if not settings.github_token:
        return {"error": "GITHUB_TOKEN not configured"}

    pool = await get_pg_pool()
    stats = {"postman_chunks": 0, "bruno_chunks": 0}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for source_name, config in COLLECTION_REPOS.items():
            repo = config["repo"]
            path = config["path"]
            fmt = config["format"]

            print(f"\n--- {source_name} ({repo}/{path}) ---")
            files = await fetch_github_dir(client, repo, path)

            if fmt == "postman_v2":
                for file_info in files:
                    if not file_info.get("name", "").endswith(".json"):
                        continue
                    content = await fetch_github_file(client, repo, file_info["path"])
                    if not content:
                        continue
                    chunks = parse_postman_collection(content)
                    async with pool.acquire() as conn:
                        for chunk in chunks:
                            await conn.execute("""
                                INSERT INTO embeddings (content, source_type, service_name, metadata, source_url)
                                VALUES ($1, 'postman', $2, $3, $4)
                            """,
                                chunk["content"],
                                chunk["metadata"].get("collection", source_name),
                                json.dumps(chunk["metadata"]),
                                f"github://{repo}/{file_info['path']}",
                            )
                            stats["postman_chunks"] += 1
                    print(f"  ✓ {file_info['name']}: {len(chunks)} requests")

            elif fmt == "bruno":
                # Bruno files are in subdirectories
                for dir_info in files:
                    if dir_info.get("type") != "dir":
                        continue
                    bru_files = await fetch_github_dir(client, repo, dir_info["path"])
                    for bru_file in bru_files:
                        if not bru_file.get("name", "").endswith(".bru"):
                            continue
                        content = await fetch_github_file(client, repo, bru_file["path"])
                        if not content:
                            continue
                        chunk = parse_bruno_file(content, bru_file["name"])
                        if chunk:
                            async with pool.acquire() as conn:
                                await conn.execute("""
                                    INSERT INTO embeddings (content, source_type, service_name, metadata, source_url)
                                    VALUES ($1, 'bruno', $2, $3, $4)
                                """,
                                    chunk["content"],
                                    dir_info["name"],
                                    json.dumps(chunk["metadata"]),
                                    f"github://{repo}/{bru_file['path']}",
                                )
                                stats["bruno_chunks"] += 1

    print(f"\n✓ Collection indexing complete: {stats['postman_chunks']} postman, {stats['bruno_chunks']} bruno")
    return stats


async def main():
    await collect_postman_bruno()


if __name__ == "__main__":
    asyncio.run(main())
