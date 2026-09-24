import importlib
import os
import sys

from fastapi.testclient import TestClient


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        assert url.endswith("/api/wireguard")
        return _FakeResponse(
            {
                "enabled": True,
                "requested_enabled": True,
                "interface": "wg0",
                "status": "connected",
                "vpn_route": True,
            }
        )


def load_app(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost:8088/api/spotify/callback")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://worker:8090")

    (tmp_path / "static" / "assets").mkdir(parents=True)
    (tmp_path / "templates").mkdir()
    repo = os.path.dirname(os.path.dirname(__file__))
    monkeypatch.chdir(tmp_path)
    sys.path.insert(0, os.path.join(repo, "web"))
    sys.path.insert(0, repo)

    import database
    database.DB_PATH = str(db_path)
    import app

    importlib.reload(app)
    return app


def test_wireguard_websocket_accepts_connection_and_pushes_state(tmp_path, monkeypatch):
    app = load_app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.httpx, "AsyncClient", _FakeAsyncClient)

    with TestClient(app.app) as client:
        with client.websocket_connect("/ws/wireguard") as websocket:
            payload = websocket.receive_json()

    assert payload["type"] == "wireguard"
    assert payload["enabled"] is True
    assert payload["requested_enabled"] is True
    assert payload["interface"] == "wg0"
    assert payload["status"] == "connected"


def test_wireguard_websocket_route_is_registered():
    from web.app import app

    routes = {
        route.path
        for route in app.routes
        if getattr(route, "path", None)
    }
    assert "/ws/wireguard" in routes
