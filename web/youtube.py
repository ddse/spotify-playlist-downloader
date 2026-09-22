import os
import httpx
import time

WORKER_ENDPOINT = os.getenv("WORKER_ENDPOINT", "http://worker:8090")
PAGE_SIZE = 10


async def search(query: str, page: int = 1, limit: int = PAGE_SIZE, source: str = "youtube", wireguard: bool = False, debug: bool = False):
    query = query.strip()
    page = max(1, int(page))
    limit = max(1, min(int(limit), PAGE_SIZE))
    if not query:
        return {"items": [], "page": page, "limit": limit, "has_more": False}

    async with httpx.AsyncClient(timeout=60) as client:
        started = time.time()
        response = await client.get(
            f"{WORKER_ENDPOINT}/api/search/youtube",
            params={
                "q": query,
                "page": page,
                "limit": limit,
                "source": source,
                "wireguard": "1" if wireguard else "0",
                "debug": "1" if debug else "0",
            },
        )
        payload = response.json()
        if debug:
            payload.setdefault("debug", {})
            payload["debug"]["web_to_worker"] = {
                "status": "ok" if response.is_success else "error",
                "http_status": response.status_code,
                "worker_endpoint": WORKER_ENDPOINT,
                "duration_ms": round((time.time() - started) * 1000),
            }
            if not response.is_success:
                payload.setdefault("error", f"Worker search returned HTTP {response.status_code}")
                return payload
        response.raise_for_status()
        return payload
