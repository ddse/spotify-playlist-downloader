from pathlib import Path
from unittest.mock import patch

from worker.media_repair import repair_media_file


def test_repair_remux_success(tmp_path):
    source = tmp_path / "bad.mp3"; source.write_bytes(b"bad")
    destination = tmp_path / "fixed.mp3"
    valid = type("R", (), {"valid": True})()

    def fake_ffmpeg(args, timeout=300):
        Path(args[-1]).write_bytes(b"fixed")

    with patch("worker.media_repair._run_ffmpeg", side_effect=fake_ffmpeg) as ff,          patch("worker.media_repair.validate_media_file", return_value=valid):
        repair_media_file(source, destination)
    assert destination.exists()
    assert ff.call_count == 1


def test_repair_falls_back_to_transcode(tmp_path):
    source = tmp_path / "bad.mp3"; source.write_bytes(b"bad")
    destination = tmp_path / "fixed.mp3"
    invalid = type("R", (), {"valid": False, "reason": "decode error"})()
    valid = type("R", (), {"valid": True})()

    calls = {"n": 0}
    def fake_ffmpeg(args, timeout=300):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("copy failed")
        Path(args[-1]).write_bytes(b"fixed")

    with patch("worker.media_repair._run_ffmpeg", side_effect=fake_ffmpeg),          patch("worker.media_repair.validate_media_file", return_value=valid):
        repair_media_file(source, destination)
    assert destination.exists()


def test_repair_does_not_overwrite_source_on_failure(tmp_path):
    source = tmp_path / "bad.mp3"; source.write_bytes(b"original")
    destination = tmp_path / "fixed.mp3"
    invalid = type("R", (), {"valid": False, "reason": "still bad"})()
    def fake_ffmpeg(args, timeout=300):
        Path(args[-1]).write_bytes(b"bad")
    with patch("worker.media_repair._run_ffmpeg", side_effect=fake_ffmpeg),          patch("worker.media_repair.validate_media_file", return_value=invalid):
        try:
            repair_media_file(source, destination)
        except Exception:
            pass
    assert source.read_bytes() == b"original"
