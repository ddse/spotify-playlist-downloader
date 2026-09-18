import json
import os
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yt_dlp

PAGE_SIZE = 10
MAX_SEARCH_RESULTS = 50
HOST = os.getenv("WORKER_API_HOST", "0.0.0.0")
PORT = int(os.getenv("WORKER_API_PORT", "8090"))


def search(query: str, page: int = 1, limit: int = PAGE_SIZE):
    query = query.strip()
    page = max(1, int(page))
    limit = max(1, min(int(limit), PAGE_SIZE))
    if not query:
        return {"items": [], "page": page, "limit": limit, "has_more": False}

    end = min(MAX_SEARCH_RESULTS, page * limit)
    opts = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(f"ytsearch{end}:{query}", download=False)

    all_items = []
    for entry in data.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        video_id = entry["id"]
        url = (
            entry.get("webpage_url")
            or entry.get("original_url")
            or f"https://www.youtube.com/watch?v={video_id}"
        )
        all_items.append({
            "id": video_id,
            "title": entry.get("title") or "",
            "channel": entry.get("channel") or entry.get("uploader") or "",
            "duration": entry.get("duration"),
            "url": url,
            "thumbnail": entry.get("thumbnail")
                or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        })

    start = (page - 1) * limit
    items = all_items[start:start + limit]
    has_more = len(all_items) > start + limit or (
        page * limit < MAX_SEARCH_RESULTS and len(all_items) == page * limit
    )

    return {
        "items": items,
        "page": page,
        "limit": limit,
        "has_more": has_more,
        "max_pages": MAX_SEARCH_RESULTS // limit,
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self._json(200, {"ok": True, "service": "worker"})
        if parsed.path != "/api/search/youtube":
            return self._json(404, {"error": "not found"})

        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0]
        page = params.get("page", ["1"])[0]
        limit = params.get("limit", [str(PAGE_SIZE)])[0]

        try:
            result = search(query, int(page), int(limit))
            self._json(200, result)
        except Exception as exc:
            self._json(500, {
                "items": [],
                "page": max(1, int(page)),
                "limit": max(1, min(int(limit), PAGE_SIZE)),
                "has_more": False,
                "error": f"{type(exc).__name__}: {exc}",
            })

    def log_message(self, fmt, *args):
        print(f"[worker-api] {fmt % args}", flush=True)


def serve():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[worker-api] listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()
