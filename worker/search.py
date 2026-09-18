import json
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import wireguard as manager
from providers import PROVIDERS

PAGE_SIZE = 10
HOST = "0.0.0.0"
PORT = 8090


def search(query: str, page: int = 1, limit: int = PAGE_SIZE, source: str = "youtube", wireguard=None):
    query = query.strip()
    page = max(1, int(page))
    limit = max(1, min(int(limit), PAGE_SIZE))
    if not query:
        return {"items": [], "page": page, "limit": limit, "has_more": False}
    provider = PROVIDERS.get(source)
    if not provider:
        raise ValueError(f"unsupported search provider: {source}")
    use_wireguard = manager.setting_enabled() if wireguard is None else bool(wireguard)
    return manager.run(use_wireguard, lambda: provider(query, page, limit))


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
        if parsed.path == "/api/wireguard":
            try:
                return self._json(200, manager.status())
            except Exception as exc:
                return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
        if parsed.path == "/api/wireguard/files":
            try:
                import glob
                files = sorted(glob.glob("/etc/wireguard/*.conf"))
                return self._json(200, {
                    "directory": "/etc/wireguard",
                    "files": [
                        {"path": path, "name": path.rsplit("/", 1)[-1]}
                        for path in files
                    ],
                })
            except Exception as exc:
                return self._json(500, {"directory": "/etc/wireguard", "files": [], "error": f"{type(exc).__name__}: {exc}"})
        if parsed.path != "/api/search/youtube":
            return self._json(404, {"error": "not found"})

        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0]
        page = params.get("page", ["1"])[0]
        limit = params.get("limit", [str(PAGE_SIZE)])[0]
        source = params.get("source", ["youtube"])[0]
        wireguard = params.get("wireguard", [None])[0]
        use_wireguard = None if wireguard is None else wireguard.lower() in {"1", "true", "yes", "on"}

        try:
            result = search(query, int(page), int(limit), source, use_wireguard)
            self._json(200, result)
        except Exception as exc:
            self._json(500, {
                "items": [],
                "page": max(1, int(page)),
                "limit": max(1, min(int(limit), PAGE_SIZE)),
                "has_more": False,
                "error": f"{type(exc).__name__}: {exc}",
            })

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/wireguard":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            params = parse_qs(body)
            enabled = params.get("enabled", ["0"])[0].lower() in {"1", "true", "yes", "on"}
            manager.set_enabled(enabled)
            return self._json(200, manager.status())
        except Exception as exc:
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        print(f"[worker-api] {fmt % args}", flush=True)


def serve():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[worker-api] listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()
