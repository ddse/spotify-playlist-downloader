import importlib.util
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_provider(name):
    return load_module(
        f"mock_provider_{name}",
        pathlib.Path("worker") / "providers" / f"{name}.py",
    )


def test_nhaccuatui_search_is_fully_mocked(monkeypatch):
    nct = load_provider("nhaccuatui")
    captured = {}

    html = """
      <a href="https://www.nhaccuatui.com/bai-hat/mock-song.ABC.html">
        Mock <b>Song</b>
      </a>
      <a href="https://www.nhaccuatui.com/bai-hat/mock-song.ABC.html">duplicate</a>
      <a href="https://www.nhaccuatui.com/song/OTHER123?source=test">
        Other Song
      </a>
    """

    class Response:
        def read(self):
            return html.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_urlopen(request, timeout=20):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(nct.urllib.request, "urlopen", fake_urlopen)

    result = nct.search("mock & song", page=1, limit=10)

    assert captured["url"] == (
        "https://www.nhaccuatui.com/tim-kiem?q=mock%20%26%20song"
    )
    assert captured["timeout"] == 20
    assert len(result["items"]) == 2
    assert result["items"][0]["title"] == "Mock Song"
    assert result["items"][1]["id"].startswith(
        "https://www.nhaccuatui.com/song/"
    )
    assert result["items"][0]["source"] == "nhaccuatui"


def test_nhaccuatui_empty_query_never_calls_network(monkeypatch):
    nct = load_provider("nhaccuatui")

    def fail(*args, **kwargs):
        raise AssertionError("network must not be called for an empty query")

    monkeypatch.setattr(nct.urllib.request, "urlopen", fail)

    assert nct.search("   ") == {
        "items": [],
        "page": 1,
        "limit": 10,
        "has_more": False,
    }


def test_zingmp3_search_uses_mock_http_only(monkeypatch):
    zing = load_provider("zingmp3")
    calls = []

    class Response:
        ok = True
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b'{"err":0}'

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "err": 0,
                "data": {
                    "items": [
                        {
                            "encodeId": "SONG1",
                            "title": "Mock Song",
                            "link": "/bai-hat/mock-song/SONG1.html",
                            "duration": 123,
                            "artists": [{"name": "Mock Artist"}],
                        }
                    ]
                },
            }

    class Cookies:
        def get(self, key, default=None):
            return "mock-rqid" if key == "zmp3_rqid" else default

    class Session:
        def __init__(self):
            self.cookies = Cookies()
            self.headers = {}

        def get(self, url, timeout=30):
            calls.append((url, timeout))
            return Response()

    monkeypatch.setattr(zing.requests, "Session", Session)

    result = zing.search("Mock Song", page=1, limit=10)

    assert result["items"][0]["id"] == "SONG1"
    assert result["items"][0]["channel"] == "Mock Artist"
    assert len(calls) == 1
    assert "/api/v2/search/multi" in calls[0][0]
    assert calls[0][1] == 30


def test_zingmp3_stream_url_uses_mock_response(monkeypatch):
    zing = load_provider("zingmp3")

    class Response:
        ok = True
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b'{"err":0}'

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "err": 0,
                "data": {
                    "128": "https://cdn.example/mock-128.mp3",
                    "320": "https://cdn.example/mock-320.mp3",
                },
            }

    class Cookies:
        def get(self, key, default=None):
            return "mock-rqid" if key == "zmp3_rqid" else default

    class Session:
        def __init__(self):
            self.cookies = Cookies()
            self.headers = {}

        def get(self, url, timeout=30):
            assert "/api/v2/song/get/streaming" in url
            return Response()

    monkeypatch.setattr(zing.requests, "Session", Session)

    assert (
        zing.get_stream_url("SONG1")
        == "https://cdn.example/mock-128.mp3"
    )


def test_zingmp3_streaming_error_is_actionable(monkeypatch):
    zing = load_provider("zingmp3")

    class Response:
        ok = True
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b'{"err":-1110}'

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "err": -1110,
                "msg": "country restricted",
                "data": None,
            }

    class Cookies:
        def get(self, key, default=None):
            return "mock-rqid" if key == "zmp3_rqid" else default

    class Session:
        def __init__(self):
            self.cookies = Cookies()
            self.headers = {}

        def get(self, url, timeout=30):
            return Response()

    monkeypatch.setattr(zing.requests, "Session", Session)

    try:
        zing.get_stream_url("SONG1")
        assert False, "expected ZingMp3Error"
    except zing.ZingMp3Error as exc:
        message = str(exc)
        assert "streaming API" in message
        assert "-1110" in message
        assert "country restricted" in message


def test_zingmp3_invalid_json_is_reported(monkeypatch):
    zing = load_provider("zingmp3")

    class Response:
        ok = True
        status_code = 200
        headers = {"Content-Type": "text/html"}
        content = b"<html>not json</html>"

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("invalid json")

    class Cookies:
        def get(self, key, default=None):
            return "mock-rqid" if key == "zmp3_rqid" else default

    class Session:
        def __init__(self):
            self.cookies = Cookies()
            self.headers = {}

        def get(self, url, timeout=30):
            return Response()

    monkeypatch.setattr(zing.requests, "Session", Session)

    try:
        zing.search("Mock Song")
        assert False, "expected ZingMp3Error"
    except zing.ZingMp3Error as exc:
        assert str(exc) == "Zing MP3 returned invalid JSON"


def test_search_wireguard_policy_is_mocked():
    import worker.search as module
    calls = []

    def fake_run(enabled, func):
        calls.append(enabled)
        return func()

    module.manager.setting_enabled = lambda: False
    module.manager.debug_status = lambda: {"status": "mock"}
    module.manager.run = fake_run
    module.PROVIDERS["zingmp3"] = (
        lambda *args: {"items": [{"id": "mock-z"}], "page": 1, "limit": 10, "has_more": False}
    )
    module.PROVIDERS["nhaccuatui"] = (
        lambda *args: {"items": [{"id": "mock-n"}], "page": 1, "limit": 10, "has_more": False}
    )

    assert module.search("test", source="zingmp3", wireguard=True)["items"]
    assert module.search("test", source="nhaccuatui", wireguard=False)["items"]
    assert calls == [True, False]


def test_download_output_resolves_only_fresh_media(tmp_path):
    output = load_module("mock_output", pathlib.Path("worker") / "output.py")
    music = tmp_path / "music"
    folder = music / "Artist" / "Album"
    folder.mkdir(parents=True)

    old_file = folder / "Old Song.mp3"
    old_file.write_bytes(b"x" * 2048)
    old_mtime = time.time() - 60
    pathlib.Path(old_file).touch()
    import os
    os.utime(old_file, (old_mtime, old_mtime))

    fresh_file = folder / "Mock Song.mp3"
    fresh_file.write_bytes(b"x" * 2048)

    row = {
        "title": "Mock Song",
        "artists": "Artist",
        "album": "Album",
        "download_folder": "",
    }

    resolved = output.resolve_downloaded_file(row, music, time.time() - 5)

    assert resolved == str(fresh_file.resolve())


def test_download_output_rejects_small_or_non_media_files(tmp_path):
    output = load_module("mock_output_invalid", pathlib.Path("worker") / "output.py")
    music = tmp_path / "music"
    folder = music / "Artist" / "Album"
    folder.mkdir(parents=True)

    (folder / "too-small.mp3").write_bytes(b"x" * 10)
    (folder / "metadata.json").write_bytes(b"x" * 2048)
    (folder / "subtitle.srt").write_bytes(b"x" * 2048)

    row = {
        "title": "Mock Song",
        "artists": "Artist",
        "album": "Album",
        "download_folder": "",
    }

    assert output.resolve_downloaded_file(row, music, time.time() - 5) == ""
