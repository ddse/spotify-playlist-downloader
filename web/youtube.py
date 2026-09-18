import os
import httpx

WORKER_ENDPOINT = os.getenv("WORKER_ENDPOINT", "http://worker:8090")
PAGE_SIZE = 10


async def search(query: str, page: int = 1, limit: int = PAGE_SIZE, source: str = "youtube"):
    query = query.strip()
    page = max(1, int(page))
    limit = max(1, min(int(limit), PAGE_SIZE))
    if not query:
        return {"items": [], "page": page, "limit": limit, "has_more": False}

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            f"{WORKER_ENDPOINT}/api/search/youtube",
            params={"q": query, "page": page, "limit": limit, "source": source},
        )
        response.raise_for_status()
        return response.json()
