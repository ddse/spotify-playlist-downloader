import importlib.util
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))


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
    html = b'''
      <a href="https://www.nhaccuatui.com/bai-hat/test-song.XYZ.html">Test <b>Song</b></a>
      <a href="https://www.nhaccuatui.com/bai-hat/test-song.XYZ.html">duplicate</a>
    '''

    class Response:
        def read(self): return html
        def __enter__(self): return self
        def __exit__(self, *args): pass

    monkeypatch.setattr(nct.urllib.request, "urlopen", lambda *a, **k: Response())
    result = nct.search("test")
    assert len(result["items"]) == 1
    assert result["items"][0]["source"] == "nhaccuatui"
    assert result["items"][0]["title"] == "Test Song"
