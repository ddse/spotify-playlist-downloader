import sqlite3
from pathlib import Path
from unittest.mock import patch

from database import init_db
import worker.worker as worker


def test_failed_validation_does_not_mark_track_completed(tmp_path):
    c = sqlite3.connect(tmp_path / "db.sqlite")
    c.row_factory = sqlite3.Row
    init_db(c)
    media = tmp_path / "song.mp3"; media.write_bytes(b"bad")
    c.execute("INSERT INTO tracks(spotify_id,title,artists,status,file_path) VALUES(?,?,?,?,?)",
              ("t1","Song","Artist","downloading",str(media)))
    c.commit()

    invalid = type("Result", (), {"valid": False, "reason": "no audio stream found", "infrastructure_error": False})()
    with patch("worker.worker.validate_media_file", return_value=invalid),          patch("worker.worker.MEDIA_REPAIR_MAX_ATTEMPTS", 0):
        try:
            worker.validate_with_repair(c, "t1", str(media), "audio", "mp3")
        except RuntimeError:
            pass
    row = c.execute("SELECT status,media_validation_status FROM tracks WHERE spotify_id='t1'").fetchone()
    assert row["status"] == "downloading"
    assert row["media_validation_status"] == "invalid"
    c.close()


def test_new_audio_download_output_always_runs_validation(tmp_path):
    c = sqlite3.connect(tmp_path / "db.sqlite")
    c.row_factory = sqlite3.Row
    init_db(c)
    media = tmp_path / "song.mp3"; media.write_bytes(b"audio")
    c.execute("""INSERT INTO tracks(spotify_id,title,artists,status,file_path,download_type,download_format)
                 VALUES(?,?,?,?,?,?,?)""", ("t-new","Song","Artist","downloading","", "audio", "mp3"))
    c.commit()
    valid = type("Result", (), {"valid": True, "reason": None, "infrastructure_error": False})()
    with patch("worker.worker.validate_media_file", return_value=valid) as probe, \
         patch("worker.worker.rename_download_to_current_title", return_value=str(media)):
        result = worker.validate_downloaded_output(c, c.execute("SELECT * FROM tracks WHERE spotify_id='t-new'").fetchone(), "t-new", str(media), 0)
    assert result == str(media)
    probe.assert_called_once_with(str(media), "audio")
    row = c.execute("SELECT media_validation_status,media_validation_version,media_validation_at FROM tracks WHERE spotify_id='t-new'").fetchone()
    assert row["media_validation_status"] == "valid"
    assert row["media_validation_version"] == worker.CURRENT_VALIDATION_VERSION
    assert row["media_validation_at"]
    c.close()



def test_jellyfin_refresh_failure_is_non_fatal(monkeypatch):
    from worker import jellyfin
    import sqlite3
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.executemany("INSERT INTO app_settings(key,value) VALUES(?,?)", [
        ("jellyfin_enabled","1"), ("jellyfin_url","http://jellyfin:8096"), ("jellyfin_api_key","token")
    ])
    c.commit()
    def fail(*args, **kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(jellyfin.httpx, "post", fail)
    result = jellyfin.refresh_library(c)
    assert result["ok"] is False
    assert result["skipped"] is False
    assert "offline" in result["reason"]
    c.close()
