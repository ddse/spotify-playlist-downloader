import json, urllib.request
from urllib.parse import quote


def _request_json(url):
    req=urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://zingmp3.vn/",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _artists(value):
    if isinstance(value, list):
        return ", ".join(
            (a.get("name", "") if isinstance(a, dict) else str(a)).strip()
            for a in value if a
        )
    if isinstance(value, dict):
        return str(value.get("name", "")).strip()
    return str(value or "").strip()


def _collect_songs(value, out):
    """Collect song records from different Zing search response shapes."""
    if isinstance(value, dict):
        sid = value.get("encodeId") or value.get("id") or value.get("code")
        title = value.get("title") or value.get("name")
        if sid and title:
            out.append({
                "id": sid,
                "title": str(title),
                "channel": _artists(value.get("artists") or value.get("artist") or value.get("artistName")),
                "duration": value.get("duration"),
                "url": value.get("link") or (
                    f"https://zingmp3.vn/bai-hat/{value.get('alias','')}/{sid}.html"
                ),
                "thumbnail": value.get("thumbnailM") or value.get("thumbnail") or value.get("thumbnailUrl") or "",
                "source": "zingmp3",
            })
            return
        for child in value.values():
            _collect_songs(child, out)
    elif isinstance(value, list):
        for child in value:
            _collect_songs(child, out)


def _normalize(data, limit, page):
    raw = []
    _collect_songs(data, raw)
    items = []
    seen = set()
    for item in raw:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        items.append(item)
        if len(items) >= limit:
            break
    return {"items": items, "page": page, "limit": limit, "has_more": len(items) >= limit}


def search(query, page=1, limit=10):
    page = max(1, int(page)); limit = max(1, min(int(limit), 10))
    query = str(query or "").strip()
    if not query:
        return {"items": [], "page": page, "limit": limit, "has_more": False}

    url = f"https://ac.zingmp3.vn/v1/web/search?num={limit}&page={page}&query={quote(query, safe='')}"
    try:
        data = _request_json(url)
        result = _normalize(data, limit, page)
        if result["items"]:
            return result
    except Exception:
        # The legacy autocomplete endpoint is still useful when the current
        # web-search endpoint changes response shape or is temporarily empty.
        pass

    legacy = (
        "https://ac.mp3.zing.vn/complete?type=artist,song,key,code"
        f"&num={limit}&query={quote(query, safe='')}"
    )
    try:
        return _normalize(_request_json(legacy), limit, page)
    except Exception:
        return {"items": [], "page": page, "limit": limit, "has_more": False}
