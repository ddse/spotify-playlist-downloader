import importlib
import json
import os
import sys

from fastapi.testclient import TestClient


def load_app(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost:8088/api/spotify/callback")

    (tmp_path / "static" / "assets").mkdir(parents=True)
    (tmp_path / "templates").mkdir()
    repo = os.path.dirname(os.path.dirname(__file__))
    os.chdir(tmp_path)
    sys.path.insert(0, os.path.join(repo, "web"))
    sys.path.insert(0, repo)

    import database
    database.DB_PATH = str(db_path)
    import app
    importlib.reload(app)
    return app, TestClient(app.app)


def test_provider_settings_roundtrip_and_secret_mask(tmp_path, monkeypatch):
    app, client = load_app(tmp_path, monkeypatch)

    r = client.get("/api/settings/connections")
    assert r.status_code == 200
    assert set(r.json()["items"]) >= {
        "spotify", "youtube", "soundcloud", "tiktok", "apple_music",
        "zingmp3", "nhaccuatui"
    }

    r = client.put(
        "/api/settings/connections/spotify",
        json={
            "enabled": True,
            "config": {
                "client_id": "client-123",
                "client_secret": "secret-456",
                "redirect_uri": "http://localhost/callback",
            },
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["configured"] is True
    assert data["config"]["client_id"] == "client-123"
    assert data["config"]["client_secret"] == "********"

    r = client.get("/api/settings/connections")
    data = r.json()["items"]["spotify"]
    assert data["config"]["client_secret"] == "********"

    r = client.put(
        "/api/settings/connections/spotify",
        json={"enabled": True, "config": {
            "client_id": "client-123",
            "client_secret": "********",
            "redirect_uri": "http://localhost/callback",
        }},
    )
    assert r.status_code == 200

    import sqlite3
    c = sqlite3.connect(tmp_path / "app.db")
    row = c.execute(
        "SELECT config_json FROM provider_connections WHERE provider='spotify'"
    ).fetchone()
    c.close()
    cfg = json.loads(row[0])
    assert cfg["client_secret"] == "secret-456"


def test_unknown_provider_is_rejected(tmp_path, monkeypatch):
    _, client = load_app(tmp_path, monkeypatch)
    assert client.put(
        "/api/settings/connections/not-a-provider",
        json={"enabled": True, "config": {}},
    ).status_code == 404


def test_disabled_provider_test(tmp_path, monkeypatch):
    _, client = load_app(tmp_path, monkeypatch)
    r = client.put(
        "/api/settings/connections/youtube",
        json={"enabled": False, "config": {}},
    )
    assert r.status_code == 200

    r = client.post("/api/settings/connections/youtube/test")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["status"] == "disabled"
