import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from database import init_db
import web.app as web_app


TRACK_ID = "yt:https://www.nhaccuatui.com/song/gBhNmAA6MrWe:audio:mp3:320:auto::0"


def _connect_factory(tmp_path):
    path = tmp_path / "app.db"
    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        init_db(c)
        return c
    return connect


def _seed(connect, status="queued", file_path="", error=None):
    c = connect()
    c.execute("""INSERT INTO tracks(
        spotify_id,title,artists,album,spotify_url,status,progress,error,
        source_type,source_url,file_path
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (TRACK_ID, "Test song", "Test artist", "Test album",
         "https://www.nhaccuatui.com/song/gBhNmAA6MrWe", status, 87, error,
         "nhaccuatui", "https://www.nhaccuatui.com/song/gBhNmAA6MrWe", file_path))
    c.commit(); c.close()


def _client(tmp_path, monkeypatch):
    connect = _connect_factory(tmp_path)
    monkeypatch.setattr(web_app, "db", connect)
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    return connect, TestClient(web_app.app)


def test_retry_completed_track_requeues(tmp_path, monkeypatch):
    connect, client = _client(tmp_path, monkeypatch); _seed(connect, "completed")
    r = client.post("/api/retry", params={"track_id": TRACK_ID})
    assert r.status_code == 200 and r.json() == {"ok": True}
    c = connect(); row = c.execute("SELECT status,progress,error,download_speed,eta FROM tracks WHERE spotify_id=?", (TRACK_ID,)).fetchone(); c.close()
    assert tuple(row) == ("queued", 0, None, "", "")


def test_retry_failed_track_requeues_and_clears_error(tmp_path, monkeypatch):
    connect, client = _client(tmp_path, monkeypatch); _seed(connect, "failed", error="download failed")
    r = client.post("/api/retry", params={"track_id": TRACK_ID})
    assert r.status_code == 200 and r.json() == {"ok": True}
    c = connect(); row = c.execute("SELECT status,progress,error FROM tracks WHERE spotify_id=?", (TRACK_ID,)).fetchone(); c.close()
    assert tuple(row) == ("queued", 0, None)


def test_completed_file_download_and_playback_accept_encoded_track_id(tmp_path, monkeypatch):
    connect, client = _client(tmp_path, monkeypatch)
    music = tmp_path / "music"; music.mkdir(); media = music / "test.mp3"; media.write_bytes(b"fake mp3")
    _seed(connect, "completed", str(media))
    r = client.get("/api/files", params={"track_id": TRACK_ID, "download": "1"})
    assert r.status_code == 200 and r.content == b"fake mp3" and r.headers["content-disposition"].startswith("attachment")
    r = client.get("/api/files", params={"track_id": TRACK_ID, "download": "0"})
    assert r.status_code == 200 and r.content == b"fake mp3" and r.headers["content-disposition"].startswith("inline")


def test_delete_file_accepts_encoded_track_id(tmp_path, monkeypatch):
    connect, client = _client(tmp_path, monkeypatch)
    music = tmp_path / "music"; music.mkdir(); media = music / "test.mp3"; media.write_bytes(b"fake mp3")
    _seed(connect, "completed", str(media))
    r = client.delete("/api/files", params={"track_id": TRACK_ID})
    assert r.status_code == 200 and r.json() == {"ok": True} and not media.exists()


def test_queue_actions_accept_encoded_track_id_as_query_parameter(tmp_path, monkeypatch):
    connect, client = _client(tmp_path, monkeypatch); _seed(connect, "queued")
    assert client.post("/api/queue/pause", params={"track_id": TRACK_ID}).json() == {"ok": True}
    assert client.post("/api/queue/start", params={"track_id": TRACK_ID}).json() == {"ok": True}
    assert client.post("/api/queue/prioritize", params={"track_id": TRACK_ID}).json() == {"ok": True}
    c = connect(); row = c.execute("SELECT status,auto_start,priority FROM tracks WHERE spotify_id=?", (TRACK_ID,)).fetchone(); c.close()
    assert tuple(row) == ("queued", 1, 1)


def test_delete_queued_track_accepts_encoded_track_id(tmp_path, monkeypatch):
    connect, client = _client(tmp_path, monkeypatch); _seed(connect, "queued")
    r = client.delete("/api/queue", params={"track_id": TRACK_ID})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_frontend_track_actions_use_query_parameters():
    source = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx"
    text = source.read_text(encoding="utf-8")
    assert "/api/files?track_id=" in text
    assert "/api/retry?track_id=" in text
    assert "/api/queue/start?track_id=" in text
    assert "/api/queue/pause?track_id=" in text
    assert "/api/queue/prioritize?track_id=" in text
    assert "/api/queue?track_id=" in text
    assert 'href={"/api/files/'+ not in text
    assert 'href={"/api/retry/'+ not in text
    assert "api('/api/queue/" not in text
    assert "fetch('/api/queue?track_id=bulk'" not in text
    assert "fetch('/api/queue/bulk'" in text


def test_link_actions_are_not_buttons_nested_inside_anchors():
    source = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx"
    text = source.read_text(encoding="utf-8")
    assert '<a href={"/api/files?' not in text
    assert '<a href={x.source_url}' not in text
    assert '<a href={item.url}' not in text
    assert '<a href={p.url}' not in text
    assert 'function Btn({children,onClick,href' in text


def test_frontend_bulk_actions_use_bulk_endpoint():
    source = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx"
    text = source.read_text(encoding="utf-8")
    assert "fd.append('action',action)" in text
    assert "fd.append('ids',selected.join(','))" in text
    assert "fetch('/api/queue/bulk',{method:'POST',body:fd})" in text
    assert "fetch('/api/queue?track_id=bulk'" not in text
