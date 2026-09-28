import sqlite3
import threading
from unittest.mock import patch
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from database import init_db
from worker.search import Handler


def test_worker_media_validate_api_persists_valid_status(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    import database
    database.DB_PATH = str(db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    init_db(c)
    media = tmp_path / "song.mp3"
    media.write_bytes(b"media")
    c.execute("""INSERT INTO tracks(spotify_id,title,artists,status,file_path,download_type)
                 VALUES(?,?,?,?,?,?)""", ("t1","Song","Artist","completed",str(media),"audio"))
    c.commit(); c.close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        valid = type("R", (), {"valid": True, "reason": None})()
        with patch("worker.search.validate_media_file", return_value=valid):
            req = Request(f"http://127.0.0.1:{server.server_port}/api/media/validate?track_id=t1", method="POST")
            with urlopen(req, timeout=5) as response:
                assert response.status == 200
        c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
        row = c.execute("SELECT media_validation_status,media_validation_version FROM tracks WHERE spotify_id='t1'").fetchone()
        assert row["media_validation_status"] == "valid"
        assert row["media_validation_version"] > 0
        c.close()
    finally:
        server.shutdown()
        server.server_close()
