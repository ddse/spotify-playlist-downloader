import hashlib
import hmac
import json
import re
import urllib.parse
import requests


DOMAIN = "https://zingmp3.vn"
API_KEY = "X5BM3w8N7MKozC0B85o4KMlzLZKhV00y"
API_SECRET = b"acOrvUS15XRW2o9JksiK1KgQ6Vbds8ZW"
API_VERSION = "1.19.1"
SIGNED_PARAMS = frozenset({"ctime", "id", "type", "page", "count", "version"})
TIMEOUT = 30
PAGE_SIZE = 10


class ZingMp3Error(RuntimeError):
    pass


def _session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://zingmp3.vn/",
        "Origin": "https://zingmp3.vn",
    })
    return session


def build_api_url(path, params):
    all_params = {**params, "ctime": "1", "version": API_VERSION}
    signed = {
        key: value
        for key, value in sorted(all_params.items())
        if key in SIGNED_PARAMS and value not in (None, "")
    }
    canonical = "".join(
        f"{urllib.parse.quote(str(key), safe='')}="
        f"{urllib.parse.quote(str(value), safe='')}"
        for key, value in signed.items()
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    signature = hmac.new(
        API_SECRET, f"{path}{digest}".encode(), hashlib.sha512
    ).hexdigest()
    query = urllib.parse.urlencode({
        **all_params,
        "apiKey": API_KEY,
        "sig": signature,
    })
    return f"{DOMAIN}{path}?{query}"


def _initialize(session):
    if session.cookies.get("zmp3_rqid"):
        return
    # Zing returns err=-201 for an empty song id; the response is still
    # useful because it sets the visitor cookie required by subsequent APIs.
    url = build_api_url("/api/v2/page/get/song", {"id": ""})
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    if not session.cookies.get("zmp3_rqid"):
        raise ZingMp3Error("Zing MP3 session cookie zmp3_rqid was not created")


def _api(session, path, params):
    _initialize(session)
    response = session.get(build_api_url(path, params), timeout=TIMEOUT)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise ZingMp3Error("Zing MP3 returned invalid JSON") from exc
    if data.get("err") != 0:
        raise ZingMp3Error(
            f"Zing MP3 API error {data.get('err')}: {data.get('msg') or 'unknown error'}"
        )
    return data


def _artists(value):
    if isinstance(value, list):
        return ", ".join(
            (a.get("name", "") if isinstance(a, dict) else str(a)).strip()
            for a in value if a
        )
    if isinstance(value, dict):
        return str(value.get("name", "")).strip()
    return str(value or "").strip()


def _collect_song_records(value, out):
    if isinstance(value, dict):
        link = value.get("link") or ""
        sid = value.get("encodeId") or value.get("id") or value.get("code")
        title = value.get("title") or value.get("name")
        # Search returns albums alongside songs. Only /bai-hat/ records are
        # download candidates; album records must not become fake songs.
        is_song = "/bai-hat/" in link or (
            value.get("encodeId") and (
                value.get("duration") is not None or
                value.get("artists") is not None
            )
        )
        if sid and title and is_song:
            out.append({
                "id": sid,
                "title": str(title),
                "channel": _artists(
                    value.get("artists")
                    or value.get("artist")
                    or value.get("artistName")
                    or value.get("artistsNames")
                ),
                "duration": value.get("duration"),
                "url": link if link.startswith("http") else (
                    f"{DOMAIN}{link}" if link else
                    f"{DOMAIN}/bai-hat/{value.get('alias', '')}/{sid}.html"
                ),
                "thumbnail": (
                    value.get("thumbnailM")
                    or value.get("thumbnail")
                    or value.get("thumbnailUrl")
                    or ""
                ),
                "source": "zingmp3",
            })
            return
        for child in value.values():
            _collect_song_records(child, out)
    elif isinstance(value, list):
        for child in value:
            _collect_song_records(child, out)


def _normalize(data, limit, page):
    raw = []
    _collect_song_records(data, raw)
    items = []
    seen = set()
    for item in raw:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        items.append(item)
        if len(items) >= limit:
            break
    return {
        "items": items,
        "page": page,
        "limit": limit,
        "has_more": len(items) >= limit,
    }


def search(query, page=1, limit=10):
    page = max(1, int(page))
    limit = max(1, min(int(limit), PAGE_SIZE))
    query = str(query or "").strip()
    if not query:
        return {"items": [], "page": page, "limit": limit, "has_more": False}

    session = _session()
    data = _api(session, "/api/v2/search/multi", {
        "q": query,
        "allowCorrect": "1",
    })
    return _normalize(data, limit, page)


def _song_id(source):
    source = str(source or "").strip()
    if not source:
        raise ZingMp3Error("Zing MP3 source is empty")
    match = re.search(r"/bai-hat/[^/]+/([^/?#]+?)(?:\.html)?(?:[?#]|$)", source)
    if match:
        return match.group(1)
    return source


def get_song(song_id, session=None):
    session = session or _session()
    data = _api(session, "/api/v2/page/get/song", {"id": _song_id(song_id)})
    song = data.get("data")
    if not isinstance(song, dict):
        raise ZingMp3Error("Zing MP3 song metadata is missing")
    return song


def get_stream_url(source, session=None):
    session = session or _session()
    song_id = _song_id(source)
    # Search results already expose encodeId. For old/full Zing URLs, resolve
    # the page first so streaming always receives the current encodeId.
    if "/" in str(source):
        song = get_song(song_id, session=session)
        song_id = song.get("encodeId") or song_id

    data = _api(session, "/api/v2/song/get/streaming", {"id": song_id})
    streams = data.get("data")
    if not isinstance(streams, dict):
        raise ZingMp3Error("Zing MP3 streaming data is missing")

    for quality in ("128", "320", "lossless"):
        value = streams.get(quality)
        if isinstance(value, str) and value.startswith("http"):
            return value

    for value in streams.values():
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, dict):
            for key in ("url", "file", "streaming"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.startswith("http"):
                    return candidate

    raise ZingMp3Error("Zing MP3 returned no downloadable audio stream")
