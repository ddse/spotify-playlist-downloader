import json, os
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from providers import PROVIDERS
PAGE_SIZE=10
HOST=os.getenv("WORKER_API_HOST","0.0.0.0")
PORT=int(os.getenv("WORKER_API_PORT","8090"))

def search(query,page=1,limit=PAGE_SIZE,source="youtube"):
    query=query.strip(); page=max(1,int(page)); limit=max(1,min(int(limit),PAGE_SIZE))
    if not query: return {"items":[],"page":page,"limit":limit,"has_more":False}
    provider=PROVIDERS.get(source)
    if not provider: raise ValueError(f"unsupported search provider: {source}")
    return provider(query,page,limit)

class Handler(BaseHTTPRequestHandler):
    def _json(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path=="/health": return self._json(200,{"ok":True,"service":"worker"})
        if parsed.path!="/api/search/youtube": return self._json(404,{"error":"not found"})
        p=parse_qs(parsed.query); q=p.get("q",[""])[0]; page=p.get("page",["1"])[0]; limit=p.get("limit",[str(PAGE_SIZE)])[0]; source=p.get("source",["youtube"])[0]
        try: return self._json(200,search(q,int(page),int(limit),source))
        except Exception as exc: return self._json(500,{"items":[],"page":max(1,int(page)),"limit":max(1,min(int(limit),PAGE_SIZE)),"has_more":False,"error":f"{type(exc).__name__}: {exc}"})
    def log_message(self,fmt,*args): print(f"[worker-api] {fmt % args}",flush=True)

def serve():
    server=ThreadingHTTPServer((HOST,PORT),Handler); print(f"[worker-api] listening on {HOST}:{PORT}",flush=True); server.serve_forever()
