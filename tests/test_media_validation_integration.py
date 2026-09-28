import sqlite3
from unittest.mock import patch

from database import init_db
import worker.worker as worker
from worker.media_validation import CURRENT_VALIDATION_VERSION, file_fingerprint


def _db(tmp_path):
    c = sqlite3.connect(tmp_path / "app.db")
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


def test_validation_state_persists_across_database_reopen(tmp_path):
    c = _db(tmp_path)
    media = tmp_path / "song.mp3"
    media.write_bytes(b"media")
    size, mtime = file_fingerprint(media)
    c.execute("""INSERT INTO tracks(spotify_id,title,artists,status,file_path,
        media_validation_status,media_validation_version,media_validation_size,media_validation_mtime_ns)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        ("t1","Song","Artist","completed",str(media),"valid",
         CURRENT_VALIDATION_VERSION,size,mtime))
    c.commit(); c.close()

    c = _db(tmp_path)
    row = c.execute("SELECT media_validation_status,media_validation_version,media_validation_size,media_validation_mtime_ns FROM tracks WHERE spotify_id='t1'").fetchone()
    assert row["media_validation_status"] == "valid"
    assert row["media_validation_version"] == CURRENT_VALIDATION_VERSION
    c.close()


def test_restart_reconciliation_does_not_reprobe_unchanged_valid_file(tmp_path):
    c = _db(tmp_path)
    media = tmp_path / "song.mp3"
    media.write_bytes(b"media")
    size, mtime = file_fingerprint(media)
    c.execute("""INSERT INTO tracks(spotify_id,title,artists,status,file_path,
        media_validation_status,media_validation_version,media_validation_size,media_validation_mtime_ns)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        ("t1","Song","Artist","completed",str(media),"valid",
         CURRENT_VALIDATION_VERSION,size,mtime))
    c.commit()
    with patch("worker.worker.validate_media_file") as probe:
        worker.reconcile_media_validation(c)
    probe.assert_not_called()
    row = c.execute("SELECT media_validation_status FROM tracks WHERE spotify_id='t1'").fetchone()
    assert row["media_validation_status"] == "valid"
    c.close()


def test_restart_reconciliation_marks_changed_file_unchecked(tmp_path):
    c = _db(tmp_path)
    media = tmp_path / "song.mp3"
    media.write_bytes(b"media")
    size, mtime = file_fingerprint(media)
    c.execute("""INSERT INTO tracks(spotify_id,title,artists,status,file_path,
        media_validation_status,media_validation_version,media_validation_size,media_validation_mtime_ns)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        ("t1","Song","Artist","completed",str(media),"valid",
         CURRENT_VALIDATION_VERSION,size,mtime))
    c.commit()
    media.write_bytes(b"changed")
    worker.reconcile_media_validation(c)
    row = c.execute("SELECT media_validation_status FROM tracks WHERE spotify_id='t1'").fetchone()
    assert row["media_validation_status"] == "unchecked"
    c.close()


def test_restart_recovery_resets_in_progress_states(tmp_path):
    c = _db(tmp_path)
    for i, state in enumerate(("checking", "repairing"), start=1):
        media = tmp_path / f"{i}.mp3"; media.write_bytes(b"media")
        c.execute("""INSERT INTO tracks(spotify_id,title,artists,status,file_path,media_validation_status)
            VALUES(?,?,?,?,?,?)""", (str(i), "Song", "Artist", "completed", str(media), state))
    c.commit()
    worker.reconcile_media_validation(c)
    states = [r[0] for r in c.execute("SELECT media_validation_status FROM tracks ORDER BY spotify_id")]
    assert states == ["unchecked", "unchecked"]
    c.close()


def test_database_migration_adds_all_media_columns(tmp_path):
    c = _db(tmp_path)
    cols = {r[1] for r in c.execute("PRAGMA table_info(tracks)")}
    expected = {
        "media_validation_status","media_validation_at","media_validation_version",
        "media_validation_error","media_validation_attempts","media_repair_attempts",
        "media_validation_size","media_validation_mtime_ns","media_validation_sha256",
    }
    assert expected <= cols
    c.close()
