import importlib.util
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]

def load_provider(name):
    spec = importlib.util.spec_from_file_location(
        f"provider_{name}", ROOT / "worker" / "providers" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_modules_have_search():
    for name in ("youtube", "zingmp3", "nhaccuatui"):
        provider = load_provider(name)
        assert callable(provider.search)


def test_search_input_is_safe_for_url_builders(monkeypatch):
    zing = load_provider("zingmp3")
    seen = {}

    class Response:
        def read(self):
            return b'{"data":{"items":[]}}'
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def fake_urlopen(req, timeout=20):
        seen["url"] = req.full_url
        return Response()

    monkeypatch.setattr(zing.urllib.request, "urlopen", fake_urlopen)
    result = zing.search("a song & artist / test", page=1, limit=10)
    assert result["items"] == []
    assert "a%20song%20%26%20artist%20/%20test" not in seen["url"]
    assert "a%20song%20%26%20artist%20%2F%20test" in seen["url"]


def test_nhaccuatui_parser_normalizes_results(monkeypatch):
    nct = load_provider("nhaccuatui")
    html = '''
      <a href="https://www.nhaccuatui.com/bai-hat/test-song.XYZ.html">Test <b>Song</b></a>
      <a href="https://www.nhaccuatui.com/bai-hat/test-song.XYZ.html">duplicate</a>
    '''

    class Response:
        def read(self): return html.encode('utf-8')
        def __enter__(self): return self
        def __exit__(self, *args): pass

    monkeypatch.setattr(nct.urllib.request, "urlopen", lambda *a, **k: Response())
    result = nct.search("test")
    assert len(result["items"]) == 1
    assert result["items"][0]["source"] == "nhaccuatui"
    assert result["items"][0]["title"] == "Test Song"



def test_zingmp3_signed_search_filters_albums(monkeypatch):
    zing = load_provider("zingmp3")
    calls = []

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {
                "err": 0,
                "data": {
                    "items": [
                        {"encodeId": "SONG1", "title": "Song", "link": "/bai-hat/song/SONG1.html",
                         "duration": 100, "artists": [{"name": "Artist"}]},
                        {"encodeId": "ALBUM1", "title": "Song (Single)", "link": "/album/song-single/ALBUM1.html"},
                    ]
                },
            }

    class Cookies:
        def get(self, key, default=None):
            return "test-rqid" if key == "zmp3_rqid" else default

    class Session:
        def __init__(self):
            self.cookies = Cookies()
            self.headers = {}
        def get(self, url, timeout=30):
            calls.append(url)
            return Response()

    monkeypatch.setattr(zing.requests, "Session", Session)
    result = zing.search("Song")
    assert [item["id"] for item in result["items"]] == ["SONG1"]
    assert result["items"][0]["channel"] == "Artist"
    assert any("/api/v2/search/multi" in url for url in calls)
    assert "q=Song" in calls[-1]
    assert "sig=" in calls[-1]


def test_zingmp3_signature_is_stable():
    zing = load_provider("zingmp3")
    url = zing.build_api_url("/api/v2/search/multi", {"q": "Hoa Vo Sac", "allowCorrect": "1"})
    assert "ctime=1" in url
    assert "version=1.19.1" in url
    assert "apiKey=" in url
    assert "sig=" in url


def test_zingmp3_streaming_prefers_128(monkeypatch):
    zing = load_provider("zingmp3")

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"err": 0, "data": {
                "128": "https://cdn.example/128.mp3",
                "320": "VIP",
            }}

    class Cookies:
        def get(self, key, default=None):
            return "test-rqid" if key == "zmp3_rqid" else default

    class Session:
        def __init__(self):
            self.cookies = Cookies()
            self.headers = {}
        def get(self, url, timeout=30):
            return Response()

    monkeypatch.setattr(zing.requests, "Session", Session)
    assert zing.get_stream_url("aQRFouTSGHqf") == "https://cdn.example/128.mp3"


def test_nhaccuatui_parser_supports_current_song_links(monkeypatch):
    nct = load_provider("nhaccuatui")
    html = '''
      <a href="https://www.nhaccuatui.com/song/4ZPNUOHU7t?source=app"><span>Việt Nam Quê Hương Tôi</span></a>
      <a href="https://www.nhaccuatui.com/song/4ZPNUOHU7t?source=app"><span>duplicate</span></a>
    '''
    class Response:
        def read(self): return html.encode('utf-8')
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(nct.urllib.request, "urlopen", lambda *a, **k: Response())
    result = nct.search("Việt nam quê hương tôi")
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "Việt Nam Quê Hương Tôi"
