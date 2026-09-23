"""NhacCuaTui search and signed stream resolver."""

import json
import re
import urllib.request
from html import unescape
from urllib.parse import quote, urlencode


API_BASE_URL = "https://graph.nct.vn"
API_HEADERS = {
    "User-Agent": "okhttp/4.12.0",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/json",
    "x-os": "android",
}


def _clean_title(raw):
    raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", unescape(raw)).strip()


def _api_request(path, params=None):
    url = f"{API_BASE_URL}{path}"
    if params:
        url += "?" + urlencode(params)

    request = urllib.request.Request(url, headers=API_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
            import gzip
            body = gzip.decompress(body)
        payload = json.loads(body.decode("utf-8"))

    if payload.get("success") is False or payload.get("code", 0) not in (0, None):
        raise RuntimeError(f"NhacCuaTui API error: {payload}")

    return payload.get("data")


def search(query, page=1, limit=10):
    page = max(1, int(page))
    limit = max(1, min(int(limit), 10))
    query = str(query or "").strip()
    if not query:
        return {"items": [], "page": page, "limit": limit, "has_more": False}

    data = _api_request(
        "/api/v1/search/song",
        {
            "keyword": query,
            "pageindex": page,
            "pagesize": limit,
            "correct": "true",
        },
    )

    songs = data.get("songs") if isinstance(data, dict) else None
    songs = songs or []

    items = []
    for song in songs:
        key = song.get("key") or song.get("songKey") or song.get("id")
        title = song.get("name") or song.get("title")
        if not key or not title:
            continue

        # Keep the public NCT page as the source URL. The signed stream URL is
        # intentionally resolved only at download time because it expires.
        items.append({
            "id": f"https://www.nhaccuatui.com/song/{key}",
            "title": title,
            "channel": song.get("artist") or song.get("artistName") or "NhacCuaTui",
            "duration": song.get("duration"),
            "url": f"https://www.nhaccuatui.com/song/{key}",
            "thumbnail": song.get("thumbnail") or song.get("image") or "",
            "source": "nhaccuatui",
        })

    return {
        "items": items,
        "page": page,
        "limit": limit,
        "has_more": len(items) >= limit,
    }


def _extract_stream_url(value):
    """Find an actual stream URL without constructing or rewriting it."""
    if isinstance(value, str):
        if value.startswith(("http://", "https://")) and (
            "stream" in value.lower() or ".mp3" in value.lower()
        ):
            return value
        return None

    if isinstance(value, dict):
        # Prefer explicit streamURL fields. Preserve the complete signed URL.
        for key in ("streamURL", "streamUrl", "stream_url", "url"):
            candidate = value.get(key)
            found = _extract_stream_url(candidate)
            if found:
                return found

        # Prefer a 320-quality stream when the API exposes a quality map.
        for key in ("320", "320k", "320kbps", "high", "hq"):
            found = _extract_stream_url(value.get(key))
            if found:
                return found

        for candidate in value.values():
            found = _extract_stream_url(candidate)
            if found:
                return found

    if isinstance(value, list):
        for candidate in value:
            found = _extract_stream_url(candidate)
            if found:
                return found

    return None


def get_stream_url(source):
    """Resolve a public NCT song URL/key to its current signed stream URL."""
    source = str(source or "").strip()
    if not source:
        raise ValueError("NhacCuaTui source is empty")

    key = source.rstrip("/").rsplit("/", 1)[-1]
    key = key.split("?", 1)[0].split("#", 1)[0]
    if key.endswith(".html"):
        key = key[:-5]

    if not key:
        raise ValueError(f"Cannot determine NhacCuaTui song key from {source!r}")

    data = _api_request(f"/api/v1/song/detail/{quote(key, safe='')}")

    stream_url = _extract_stream_url(data)
    if not stream_url:
        raise RuntimeError(
            f"NhacCuaTui streamURL not found for key={key}; "
            "the API response did not contain a playable stream URL"
        )

    return stream_url
