import importlib.util
import pathlib
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]

def load_provider():
    spec = importlib.util.spec_from_file_location(
        "provider_nhaccuatui", ROOT / "worker" / "providers" / "nhaccuatui.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

SONG_LINK = "https://www.nhaccuatui.com/song/LX0XVH77VeER?source=app"
STREAM_128 = "https://cdn.example.test/resa/song-128.mp3?token=128"
STREAM_320 = "https://cdn.example.test/resa/song-320.mp3?token=320"

def test_search_keyword_returns_input_song_link(monkeypatch):
    nct = load_provider()
    html = '<a href="https://www.nhaccuatui.com/song/LX0XVH77VeER">Hoa Vô Sắc</a>'

    class Response:
        def read(self):
            return html.encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *args): pass

    monkeypatch.setattr(nct.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    result = nct.search("Hoa Vô Sắc")

    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "Hoa Vô Sắc"
    assert result["items"][0]["url"] == "https://www.nhaccuatui.com/song/LX0XVH77VeER"
    assert result["items"][0]["id"] == "https://www.nhaccuatui.com/song/LX0XVH77VeER"
    assert result["items"][0]["source"] == "nhaccuatui"

def test_input_link_is_parsed_without_changing_it():
    parsed = urlparse(SONG_LINK)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.nhaccuatui.com"
    assert parsed.path == "/song/LX0XVH77VeER"
    assert parsed.query == "source=app"

def test_download_selects_active_non_vip_320_stream():
    detail = {
        "key": "LX0XVH77VeER",
        "name": "Hoa Vô Sắc",
        "qualityDownload": [
            {"key": "128", "onlyVIP": False, "status": 1},
            {"key": "320", "onlyVIP": False, "status": 1},
            {"key": "lossless", "onlyVIP": True, "status": 0},
        ],
        "streamURL": [
            {"type": "128", "onlyVIP": False, "status": 1, "stream": STREAM_128},
            {"type": "320", "onlyVIP": False, "status": 1, "stream": STREAM_320},
            {"type": "lossless", "onlyVIP": True, "status": 0, "stream": "https://cdn.example.test/song.flac"},
        ],
    }
    streams = [
        item for item in detail["streamURL"]
        if item.get("status") == 1 and not item.get("onlyVIP", False) and item.get("stream")
    ]
    selected = next(
        (item["stream"] for quality in ("320", "128")
         for item in streams if item.get("type") == quality),
        None,
    )
    assert selected == STREAM_320
    assert selected != "https://a01.nct.vn/hard-coded.mp3"

def test_download_writes_file(tmp_path, monkeypatch):
    output = tmp_path / "Hoa Vo Sac.mp3"
    mp3_data = b"ID3\x04\x00\x00" + b"\x00" * 2048

    class Response:
        def __init__(self):
            self.status = 200
            self.headers = {"Content-Type": "audio/mpeg", "Content-Length": str(len(mp3_data))}
        def read(self, size=-1):
            return mp3_data if size == -1 else mp3_data[:size]
        def __enter__(self): return self
        def __exit__(self, *args): pass

    requested = []
    def fake_urlopen(request, timeout=30):
        requested.append(request.full_url)
        return Response()

    monkeypatch.setattr(importlib.import_module("urllib.request"), "urlopen", fake_urlopen)
    with open(output, "wb") as file:
        with fake_urlopen(__import__("urllib.request", fromlist=["Request"]).Request(STREAM_320), timeout=30) as response:
            file.write(response.read())

    assert requested == [STREAM_320]
    assert output.exists()
    assert output.stat().st_size == len(mp3_data)

def test_open_downloaded_file(tmp_path):
    output = tmp_path / "Hoa Vo Sac.mp3"
    mp3_data = b"ID3\x04\x00\x00" + b"\x00" * 2048
    output.write_bytes(mp3_data)
    assert output.exists()
    assert output.stat().st_size > 0
    with output.open("rb") as file:
        header = file.read(3)
    assert header == b"ID3"
