#!/usr/bin/env python3
"""Live Zing MP3 search integration test.

This intentionally exercises the same signed API flow used by the provider.
It is network-dependent and is run explicitly by CI.
"""
from worker.providers import zingmp3

QUERY = "Hoa Vo Sac"


result = zingmp3.search(QUERY, page=1, limit=10)

assert result["items"], "Zing search returned no results"
assert result["page"] == 1
assert result["limit"] == 10

for item in result["items"]:
    assert item["id"], item
    assert item["title"], item
    assert item["source"] == "zingmp3"
    assert "/album/" not in item["url"]

first = result["items"][0]
print("QUERY       :", QUERY)
print("RESULTS     :", len(result["items"]))
print("FIRST ID    :", first["id"])
print("FIRST TITLE :", first["title"])
print("FIRST ARTIST:", first["channel"])
print("FIRST URL   :", first["url"])
print()
print("ALL SEARCH TESTS PASSED")
