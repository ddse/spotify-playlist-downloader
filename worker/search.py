import json
import os
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yt_dlp

import re
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

def is_nhaccuatui(url):
    try:
        host = (urlparse(url).hostname or '').lower()
        return host == 'nhaccuatui.com' or host.endswith('.nhaccuatui.com')
    except Exception:
        return False

def resolve_nhaccuatui(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode('utf-8', errors='ignore')
    match = re.search(r'player\.peConfig\.xmlURL\s*=\s*"([^"]+)"', html)
    if not match:
        raise RuntimeError('NhacCuaTui: player XML URL not found')
    xml_url = match.group(1).replace('\\/', '/')
    req = urllib.request.Request(xml_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        xml_data = r.read()
    root = ET.fromstring(xml_data)
    tracks = root.findall('.//track')
    if not tracks:
        raise RuntimeError('NhacCuaTui: no track found in XML')
    track = tracks[0]
    def value(name):
        node = track.find(name)
        return (node.text or '').strip() if node is not None else ''
    direct = value('location')
    title = value('title') or url.rstrip('/').split('/')[-1].split('.')[0]
    if not direct:
        raise RuntimeError('NhacCuaTui: direct audio URL not found')
    return {'url': direct, 'title': title}


PAGE_SIZE = 10
MAX_SEARCH_RESULTS = 50
HOST = os.getenv("WORKER_API_HOST", "0.0.0.0")
PORT = int(os.getenv("WORKER_API_PORT", "8090"))


def search_zingmp3(query: str, page: int, limit: int):
    import json as _json
    from urllib.parse import quote
    api_url = f"https://ac.zingmp3.vn/v1/web/search?num={limit}&page={page}&query={quote(query)}"
    req = urllib.request.Request(api_url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://zingmp3.vn/',
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        data = _json.loads(r.read().decode('utf-8', errors='ignore'))
    items = []
    for section in (data.get('data') or {}).get('items') or []:
        if not isinstance(section, dict):
            continue
        for entry in section.get('song') or section.get('items') or []:
            if not isinstance(entry, dict):
                continue
            song_id = entry.get('encodeId') or entry.get('id')
            if not song_id:
                continue
            items.append({
                'id': song_id,
                'title': entry.get('title') or '',
                'channel': ', '.join(a.get('name','') for a in entry.get('artists') or []),
                'duration': entry.get('duration'),
                'url': f"https://zingmp3.vn/bai-hat/{entry.get('alias','')}/{song_id}.html",
                'thumbnail': entry.get('thumbnailM') or entry.get('thumbnail') or '',
                'source': 'zingmp3',
            })
    return {'items': items[:limit], 'page': page, 'limit': limit,
            'has_more': len(items) >= limit}


def search_nhaccuatui(query: str, page: int, limit: int):
    from html import unescape
    from urllib.parse import quote
    search_url = f"https://www.nhaccuatui.com/tim-kiem?q={quote(query)}"
    req = urllib.request.Request(search_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode('utf-8', errors='ignore')
    # NCT search pages expose song links in href attributes.
    pattern = re.compile(r'href=["\'](https?://(?:www\.)?nhaccuatui\.com/bai-hat/[^"\']+\.html)["\'][^>]*>(.*?)</a>',
                         re.I | re.S)
    seen, items = set(), []
    for url, raw_title in pattern.findall(html):
        if url in seen:
            continue
        seen.add(url)
        title = re.sub(r'<[^>]+>', ' ', raw_title)
        title = re.sub(r'\\s+', ' ', unescape(title)).strip()
        if not title:
            continue
        items.append({
            'id': url,
            'title': title,
            'channel': 'NhacCuaTui',
            'duration': None,
            'url': url,
            'thumbnail': '',
            'source': 'nhaccuatui',
        })
        if len(items) >= page * limit:
            break
    start = (page - 1) * limit
    return {'items': items[start:start + limit], 'page': page, 'limit': limit,
            'has_more': len(items) > start + limit or len(items) == page * limit}


def search(query: str, page: int = 1, limit: int = PAGE_SIZE, source: str = "youtube"):
    query = query.strip()
    page = max(1, int(page))
    limit = max(1, min(int(limit), PAGE_SIZE))
    if not query:
        return {"items": [], "page": page, "limit": limit, "has_more": False}

    # Search native sources by keyword. Zing MP3 uses its public search API;
    # NhacCuaTui uses its public search page and extracts song URLs.
    if source == 'zingmp3':
        return search_zingmp3(query, page, limit)
    if source == 'nhaccuatui':
        return search_nhaccuatui(query, page, limit)

    end = min(MAX_SEARCH_RESULTS, page * limit)
    source = 'youtube'
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
            result = search(query, int(page), int(limit), source=source)
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
