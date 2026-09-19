import json
import re
import urllib.request
from urllib.parse import quote


def _request_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://zingmp3.vn/",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
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
    if isinstance(value, dict):
        sid = value.get("encodeId") or value.get("id") or value.get("code")
        title = value.get("title") or value.get("name")
        if sid and title:
            out.append({
                "id": sid,
                "title": str(title),
                "channel": _artists(value.get("artists") or value.get("artist") or value.get("artistName")),
                "duration": value.get("duration"),
                "url": value.get("link") or f"https://zingmp3.vn/bai-hat/{value.get('alias','')}/{sid}.html",
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


def _search_html(query, page, limit):
    url = f"https://zingmp3.vn/tim-kiem/bai-hat?q={quote(query, safe='')}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://zingmp3.vn/",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode("utf-8", "ignore")

    pattern = re.compile(
        r'<a\b([^>]*?href=["\']([^"\']*?/bai-hat/[^"\']+?\.html)[^>]*)>(.*?)</a>',
        re.I | re.S,
    )
    raw = []
    for attrs, link, body in pattern.findall(html):
        title_match = re.search(r'(?:title|data-title|reltitle)=["\']([^"\']+)["\']', attrs, re.I)
        title = title_match.group(1) if title_match else body
        title = re.sub(r"<[^>]+>", " ", title)
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            raw.append({
                "id": link.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html"),
                "title": title,
                "channel": "",
                "duration": None,
                "url": link if link.startswith("http") else "https://zingmp3.vn" + link,
                "thumbnail": "",
                "source": "zingmp3",
            })
    return _normalize({"items": raw}, limit, page)


def search(query, page=1, limit=10):
    page = max(1, int(page))
    limit = max(1, min(int(limit), 10))
    query = str(query or "").strip()
    if not query:
        return {"items": [], "page": page, "limit": limit, "has_more": False}

    endpoints = [
        (
            "https://ac.zingmp3.vn/v1/web/search?"
            f"num={limit}&page={page}&query={quote(query, safe='')}"
        ),
        (
            "https://ac.zingmp3.vn/v1/web/search?"
            f"q={quote(query, safe='')}&type=audio&page={page}&num={limit}"
        ),
        (
            "https://ac.mp3.zing.vn/complete?type=artist,song,key,code"
            f"&num={limit}&query={quote(query, safe='')}"
        ),
    ]

    for url in endpoints:
        try:
            result = _normalize(_request_json(url), limit, page)
            if result["items"]:
                return result
        except Exception:
            continue

    try:
        return _search_html(query, page, limit)
    except Exception:
        return {"items": [], "page": page, "limit": limit, "has_more": False}
