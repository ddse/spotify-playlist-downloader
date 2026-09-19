#!/usr/bin/env python3
"""Live NhacCuaTui integration test based on carlylekatto/nct.js.

The reference client uses:
  BASE_URL = https://graph.nct.vn
  GET /api/v1/search/song
  GET /api/v1/song/detail/{songKey}

This script validates both the NCT API contract used by nct.js and the
application's NhacCuaTui search provider.
"""

import json
import re
import urllib.request
from urllib.parse import urlencode

from worker.providers import nhaccuatui


BASE_URL = "https://graph.nct.vn"
QUERY = "Hoa Vo Sac"
PAGE = 1
LIMIT = 10

HEADERS = {
    "User-Agent": "okhttp/4.12.0",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/json",
    "x-os": "android",
}


def nct_request(path, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urlencode(params)

    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200, f"NCT API HTTP {response.status}"
        payload = json.loads(response.read().decode("utf-8"))

    assert payload.get("success") is not False, payload
    assert payload.get("code", 0) == 0, payload
    return payload.get("data")


def test_nct_search():
    data = nct_request(
        "/api/v1/search/song",
        {
            "keyword": QUERY,
            "pageindex": PAGE,
            "pagesize": LIMIT,
            "correct": "true",
        },
    )

    assert isinstance(data, dict), type(data)
    songs = data.get("songs") or []
    assert songs, "nct.js-compatible API returned no songs"

    first = songs[0]
    key = first.get("key")
    assert key, first
    assert first.get("name") or first.get("title"), first

    return first


def test_nct_song_detail(song):
    key = song["key"]
    data = nct_request(f"/api/v1/song/detail/{key}")

    assert isinstance(data, dict), type(data)
    assert data.get("key") or key
    assert data.get("name") or data.get("title"), data

    # nct.js exposes stream URLs from the cleaned song object. The API can
    # return different stream field names, so only validate when present.
    streams = data.get("streams")
    if streams is not None:
        assert isinstance(streams, (dict, list)), type(streams)

    return data


def test_application_provider():
    result = nhaccuatui.search(QUERY, page=PAGE, limit=LIMIT)

    assert result["page"] == PAGE
    assert result["limit"] == LIMIT
    assert isinstance(result["items"], list)
    assert result["items"], "application NhacCuaTui provider returned no results"

    for item in result["items"]:
        assert item["id"], item
        assert item["title"], item
        assert item["source"] == "nhaccuatui"
        assert re.match(
            r"^https?://(?:www\.)?nhaccuatui\.com/(?:bai-hat|song)/",
            item["url"],
            re.I,
        ), item
        assert item["url"] == item["id"], item

    return result


song = test_nct_search()
detail = test_nct_song_detail(song)
provider_result = test_application_provider()

print("QUERY        :", QUERY)
print("NCT SONG KEY :", song["key"])
print("NCT TITLE    :", song.get("name") or song.get("title"))
print("NCT DETAIL   :", detail.get("name") or detail.get("title"))
print("NCT STREAMS  :", bool(detail.get("streams")))
print("PROVIDER     : NhacCuaTui")
print("RESULTS      :", len(provider_result["items"]))
print("FIRST TITLE  :", provider_result["items"][0]["title"])
print("FIRST URL    :", provider_result["items"][0]["url"])
print()
print("ALL NHACCUATUI TESTS PASSED")
