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
    assert 'href={"/api/files/' not in text
    assert 'href={"/api/retry/' not in text
    for prefix in ("/api/queue/start", "/api/queue/pause", "/api/queue/prioritize"):
        assert f"{prefix}/'+" not in text
    assert "fetch('/api/queue?track_id=bulk'" not in text
    assert "fetch('/api/queue/bulk'" in text


def test_completed_download_uses_fetch_blob_action():
    source = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx"
    text = source.read_text(encoding="utf-8")
    assert "const downloadFile=async(trackId)=>" in text
    assert "fetch(endpoint,{cache:'no-store'})" in text
    assert "await response.blob()" in text
    assert "anchor.download=filename" in text
    assert "catch(error)" in text


def test_index_disables_html_cache():
    source = Path(__file__).resolve().parents[1] / "web" / "app.py"
    text = source.read_text(encoding="utf-8")
    assert "Cache-Control" in text
    assert "no-store, no-cache, must-revalidate, max-age=0" in text


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


def test_update_track_title_renames_completed_file(tmp_path, monkeypatch):
    connect, client = _client(tmp_path, monkeypatch)
    music = tmp_path / "music"; music.mkdir(); media = music / "old-name.mp3"; media.write_bytes(b"fake mp3")
    _seed(connect, "completed", str(media))
    r = client.post("/api/tracks/title", params={"track_id": TRACK_ID}, data={"title": "New Song / Live"})
    assert r.status_code == 200
    payload = r.json()
    renamed = music / "New Song _ Live.mp3"
    assert payload["ok"] is True and payload["title"] == "New Song _ Live"
    assert renamed.exists() and not media.exists()
    c = connect(); row = c.execute("SELECT title,title_override,file_path FROM tracks WHERE spotify_id=?", (TRACK_ID,)).fetchone(); c.close()
    assert tuple(row) == ("New Song _ Live", 1, str(renamed))


def test_update_track_title_rejects_downloading_track(tmp_path, monkeypatch):
    connect, client = _client(tmp_path, monkeypatch); _seed(connect, "downloading")
    r = client.post("/api/tracks/title", params={"track_id": TRACK_ID}, data={"title": "New title"})
    assert r.status_code == 409


def test_download_request_persists_title_override(tmp_path, monkeypatch):
    connect, client = _client(tmp_path, monkeypatch)
    async def fake_wireguard_enabled():
        return False
    monkeypatch.setattr(web_app, "wireguard_enabled", fake_wireguard_enabled)
    r = client.post("/api/download", data={
        "source_url": "https://www.youtube.com/watch?v=test123",
        "title": "My Custom Song",
        "title_override": "1",
        "artists": "Artist", "album": "Album", "youtube_id": "test123",
        "source_mode": "single", "download_type": "audio", "download_format": "mp3",
        "download_quality": "320", "video_codec": "auto", "download_folder": "",
        "thumbnail": "1", "subtitle": "0", "subtitle_lang": "ja,en",
        "subtitle_mode": "prefer_manual", "playlist_item_limit": "0",
        "split_chapters": "0", "auto_start": "0", "wireguard": "0",
    })
    assert r.status_code == 200
    c = connect(); row = c.execute("SELECT title,title_override FROM tracks WHERE title=?", ("My Custom Song",)).fetchone(); c.close()
    assert tuple(row) == ("My Custom Song", 1)


def test_frontend_supports_title_override_and_direct_link_metadata_ui():
    source = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx"
    text = source.read_text(encoding="utf-8")
    assert 'name="title" value={title}' in text
    assert "fd.set('title_override'" in text
    assert '/api/tracks/title?track_id=' in text
    assert 'function QueueRow' in text


def test_youtube_download_config_has_timeout_retry_and_chunk_resilience():
    source = Path(__file__).resolve().parents[1] / "worker" / "worker.py"
    text = source.read_text(encoding="utf-8")
    assert "'socket_timeout': 60" in text
    assert "'retries': 10" in text
    assert "'fragment_retries': 10" in text
    assert "'file_access_retries': 3" in text
    assert "'http_chunk_size': 10 * 1024 * 1024" in text
    assert "'retry_sleep_functions'" in text


def test_frontend_exposes_visible_edit_title_button_in_queue_and_completed():
    source = Path(__file__).resolve().parents[1] / "web" / "frontend" / "src" / "App.jsx"
    text = source.read_text(encoding="utf-8")
    assert "t('Edit name')" in text
    assert "t('Edit song title')" in text
    assert 'function CompletedTitleEditor' in text
