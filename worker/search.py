import json
import time
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import wireguard as manager
from providers import PROVIDERS

PAGE_SIZE = 10
HOST = "0.0.0.0"
PORT = 8090


def search(query: str, page: int = 1, limit: int = PAGE_SIZE, source: str = "youtube", wireguard=None, debug=False):
    started = time.time()
    query = query.strip()
    page = max(1, int(page))
    limit = max(1, min(int(limit), PAGE_SIZE))
    trace = {
        "request_received": True,
        "query": query,
        "source": source,
        "page": page,
        "limit": limit,
        "wireguard_requested": wireguard,
        "wireguard_setting_enabled": manager.setting_enabled(),
        "steps": [],
    }
    if not query:
        trace["steps"].append({"step": "validate_query", "status": "skipped", "detail": "empty query"})
        return {"items": [], "page": page, "limit": limit, "has_more": False, **({"debug": trace} if debug else {})}
    provider = PROVIDERS.get(source)
    if not provider:
        raise ValueError(f"unsupported search provider: {source}")
    use_wireguard = manager.setting_enabled() if wireguard is None else bool(wireguard)
    trace["wireguard_used"] = use_wireguard
    trace["steps"].append({"step": "worker_received", "status": "ok", "detail": "search request reached worker"})
    before = manager.debug_status()
    trace["steps"].append({"step": "wireguard_before", "status": before.get("status"), "detail": before})
    provider_started = time.time()
    try:
        def call_provider():
            if debug and source == "zingmp3":
                return provider(query, page, limit, debug=True)
            return provider(query, page, limit)

        result = manager.run(use_wireguard, call_provider)
        provider_debug = result.pop("_provider_debug", None) if isinstance(result, dict) else None
        if provider_debug:
            trace["provider_debug"] = provider_debug
        after = manager.debug_status()
        trace["steps"].append({"step": "wireguard_after", "status": after.get("status"), "detail": after})
        trace["steps"].append({"step": "provider_search", "status": "ok", "duration_ms": round((time.time() - provider_started) * 1000), "result_count": len(result.get("items", []))})
        trace["duration_ms"] = round((time.time() - started) * 1000)
        return {**result, **({"debug": trace} if debug else {})}
    except Exception as exc:
        after = manager.debug_status()
        trace["steps"].append({"step": "wireguard_after_error", "status": after.get("status"), "detail": after})
        trace["steps"].append({"step": "provider_search", "status": "error", "duration_ms": round((time.time() - provider_started) * 1000), "error": f"{type(exc).__name__}: {exc}"})
        trace["duration_ms"] = round((time.time() - started) * 1000)
        raise


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The browser/client may cancel a request while the provider is
            # still resolving. Do not turn a normal client disconnect into a
            # noisy worker traceback.
            return

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
        debug = params.get("debug", ["0"])[0].lower() in {"1", "true", "yes", "on"}

        try:
            result = search(query, int(page), int(limit), source, use_wireguard, debug)
            self._json(200, result)
        except Exception as exc:
            self._json(500, {
                "items": [],
                "page": max(1, int(page)),
                "limit": max(1, min(int(limit), PAGE_SIZE)),
                "has_more": False,
                "error": f"{type(exc).__name__}: {exc}",
                **({"debug": {
                    "request_received": True,
                    "source": source,
                    "query": query,
                    "wireguard_used": use_wireguard,
                    "steps": [{
                        "step": "worker_error",
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }],
                }} if debug else {}),
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
