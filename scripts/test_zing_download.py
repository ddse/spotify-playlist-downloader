#!/usr/bin/env python3
"""Live Zing MP3 streaming/download integration test.

This validates session initialization, metadata resolution, signed streaming,
and an actual CDN audio download. No signed CDN URL is persisted.
"""
import os
import tempfile
import urllib.request

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from worker.providers import zingmp3

SOURCE = "https://zingmp3.vn/bai-hat/Hoa-Vo-Sac-Jack-K-ICM/ZWB0IFAD.html"


def validate_file(path):
    size = os.path.getsize(path)
    assert size > 10 * 1024, f"download too small: {size}"
    with open(path, "rb") as fp:
        header = fp.read(16)
    assert (
        header.startswith(b"ID3")
        or header[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
        or b"ftyp" in header[4:12]
    ), f"not recognized audio: {header!r}"
    return size


stream_url = zingmp3.get_stream_url(SOURCE)
assert stream_url.startswith("https://")

request = urllib.request.Request(
    stream_url,
    headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://zingmp3.vn/",
    },
)

with tempfile.NamedTemporaryFile(prefix="zing-test-", suffix=".mp3", delete=False) as fp:
    output = fp.name

try:
    downloaded = 0
    with urllib.request.urlopen(request, timeout=60) as response, open(output, "wb") as fp:
        assert response.status == 200
        assert (response.headers.get("Content-Type") or "").lower().startswith("audio/")
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            fp.write(chunk)
            downloaded += len(chunk)

    size = validate_file(output)
    assert downloaded == size

    print("SOURCE      :", SOURCE)
    print("STREAM      : 128/available signed stream")
    print("HTTP        : 200")
    print("BYTES       :", size)
    print("FILE        :", output)
    print()
    print("ALL DOWNLOAD TESTS PASSED")
finally:
    try:
        os.unlink(output)
    except FileNotFoundError:
        pass
