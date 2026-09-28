from fastapi.testclient import TestClient

import web.app as web_app


class FakeResponse:
    status_code = 200
    text = '{"ok":true,"status":"valid"}'
    def json(self):
        return {"ok": True, "status": "valid"}


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def post(self, url, params=None):
        assert "/api/media/validate" in url
        assert params == {"track_id": "t1"}
        return FakeResponse()


def test_validate_route_proxies_to_worker(monkeypatch):
    monkeypatch.setattr(web_app.httpx, "AsyncClient", FakeClient)
    client = TestClient(web_app.app)
    response = client.post("/api/tracks/validate", params={"track_id": "t1"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "valid"}
