import importlib.util
import pathlib
import json

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


def test_zingmp3_parser_handles_nested_search_results(monkeypatch):
    zing = load_provider("zingmp3")
    payload = {
        "data": {"items": [{"section": {"items": [{
            "encodeId": "ZWTEST01",
            "title": "Việt Nam Quê Hương Tôi",
            "alias": "viet-nam-que-huong-toi",
            "artists": [{"name": "Trọng Tấn"}],
        }]}}]}
    }

    class Response:
        def read(self): return json.dumps(payload).encode()
        def __enter__(self): return self
        def __exit__(self, *args): pass

    monkeypatch.setattr(zing.urllib.request, "urlopen", lambda *a, **k: Response())
    result = zing.search("Việt nam quê hương tôi")
    assert result["items"][0]["id"] == "ZWTEST01"
    assert result["items"][0]["title"] == "Việt Nam Quê Hương Tôi"
    assert result["items"][0]["channel"] == "Trọng Tấn"


def test_zingmp3_search_falls_back_to_legacy_endpoint(monkeypatch):
    zing = load_provider("zingmp3")
    urls = []

    class Response:
        def __init__(self, payload): self.payload = payload
        def read(self): return json.dumps(self.payload).encode()
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def fake_urlopen(req, timeout=20):
        urls.append(req.full_url)
        if "ac.zingmp3.vn/v1/web/search" in req.full_url:
            return Response({"data": {"items": []}})
        return Response({"items": [{"encodeId": "ZWTEST02", "title": "Việt Nam Quê Hương Tôi", "alias": "viet-nam-que-huong-toi", "artists": [{"name": "Thanh Thúy"}]}]})

    monkeypatch.setattr(zing.urllib.request, "urlopen", fake_urlopen)
    result = zing.search("Việt nam quê hương tôi")
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "ZWTEST02"
    assert any("ac.mp3.zing.vn/complete" in url for url in urls)


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


def test_zingmp3_parser_supports_html_fallback(monkeypatch):
    zing = load_provider("zingmp3")
    html = '''
      <a href="https://zingmp3.vn/bai-hat/viet-nam-que-huong-toi/ZWHTML01.html">
        <span>Việt Nam Quê Hương Tôi</span>
      </a>
    '''
    class Response:
        def read(self): return html.encode('utf-8')
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(zing.urllib.request, "urlopen", lambda *a, **k: Response())
    result = zing.search("Việt nam quê hương tôi")
    assert result["items"][0]["id"] == "ZWHTML01"
    assert result["items"][0]["title"] == "Việt Nam Quê Hương Tôi"


def test_search_sources_follow_wireguard_setting(monkeypatch):
    search_mod = importlib.util.spec_from_file_location("worker_search", ROOT / "worker" / "search.py")
    module = importlib.util.module_from_spec(search_mod)
    search_mod.loader.exec_module(module)

    calls = []

    def fake_run(enabled, func):
        calls.append(enabled)
        return func()

    monkeypatch.setattr(module.manager, "run", fake_run)
    monkeypatch.setattr(module.PROVIDERS["youtube"], lambda *args: {"items": [{"id": "y"}]})
    monkeypatch.setattr(module.PROVIDERS["zingmp3"], lambda *args: {"items": [{"id": "z"}]})
    monkeypatch.setattr(module.PROVIDERS["nhaccuatui"], lambda *args: {"items": [{"id": "n"}]})

    assert module.search("test", source="youtube", wireguard=True)["items"]
    assert module.search("test", source="zingmp3", wireguard=True)["items"]
    assert module.search("test", source="nhaccuatui", wireguard=True)["items"]
    assert calls == [True, True, True]

    calls.clear()
    assert module.search("test", source="zingmp3", wireguard=False)["items"]
    assert module.search("test", source="nhaccuatui", wireguard=False)["items"]
    assert calls == [False, False]
