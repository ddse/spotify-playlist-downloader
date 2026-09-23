#!/usr/bin/env python3
"""Live NhacCuaTui stream download test.

Usage:
  python scripts/test_nhaccuatui_download.py
  python scripts/test_nhaccuatui_download.py --stream-url 'https://...signed...'
  python scripts/test_nhaccuatui_download.py --output /tmp/Hoa-Vo-Sac.mp3

The stream URL is always used exactly as returned by the API. This script
never constructs or replaces the CDN hostname.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from worker.providers import nhaccuatui


QUERY = "Hoa Vo Sac"
DEFAULT_OUTPUT = "Hoa Vo Sac.mp3"


def resolve_test_stream(stream_url=None):
    if stream_url:
        return stream_url

    result = nhaccuatui.search(QUERY, page=1, limit=10)
    items = result["items"]
    if not items:
        raise RuntimeError(f"No NhacCuaTui result for {QUERY!r}")

    print(f"Search result: {items[0]['title']}")
    print(f"Source URL:    {items[0]['url']}")
    return nhaccuatui.get_stream_url(items[0]["url"])


def download(stream_url, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Stream host:   {__import__('urllib.parse', fromlist=['urlparse']).urlparse(stream_url).hostname}")
    print(f"Signed query:  {'yes' if '?' in stream_url else 'no'}")

    import urllib.request

    request = urllib.request.Request(
        stream_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.nhaccuatui.com/",
            "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.5",
        },
    )

    temp = output.with_suffix(output.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = getattr(response, "status", None)
            content_type = (response.headers.get("Content-Type") or "").lower()
            content_length = response.headers.get("Content-Length")

            print(f"HTTP status:   {status}")
            print(f"Content-Type:  {content_type or '<missing>'}")
            print(f"Content-Length:{content_length or '<missing>'}")

            if status != 200:
                raise RuntimeError(f"Unexpected HTTP status: {status}")
            if not ("audio" in content_type or "mpeg" in content_type or "octet-stream" in content_type):
                raise RuntimeError(f"Unexpected Content-Type: {content_type!r}")

            total = 0
            with temp.open("wb") as fp:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    fp.write(chunk)
                    total += len(chunk)

        if total < 10 * 1024:
            raise RuntimeError(f"Downloaded file is unexpectedly small: {total} bytes")

        with temp.open("rb") as fp:
            header = fp.read(16)

        if not (
            header.startswith(b"ID3")
            or header[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
        ):
            raise RuntimeError(f"Downloaded file does not look like MP3: {header!r}")

        temp.replace(output)
        print(f"Downloaded:    {total:,} bytes")
    except Exception:
        temp.unlink(missing_ok=True)
        raise

    return output


def ffprobe(output):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries",
        "format=format_name,duration,size:stream=codec_name,bit_rate,sample_rate,channels",
        "-of", "json",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError("ffprobe found no audio stream")

    stream = streams[0]
    bitrate = int(stream.get("bit_rate") or 0)

    print("ffprobe:")
    print(json.dumps(data, indent=2))

    if bitrate and bitrate < 250_000:
        raise RuntimeError(
            f"Expected the 320kbps stream, but ffprobe reports only {bitrate} bps"
        )

    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stream-url", help="Use an already-issued signed streamURL")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    stream_url = resolve_test_stream(args.stream_url)
    output = download(stream_url, args.output)
    ffprobe(output)

    print()
    print("PASS: NhacCuaTui signed 320kbps stream download")
    print(f"File: {output.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
