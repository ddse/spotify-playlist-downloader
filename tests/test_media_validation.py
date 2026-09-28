import sqlite3
from pathlib import Path
from unittest.mock import patch

from worker.media_validation import (
    CURRENT_VALIDATION_VERSION, file_fingerprint, needs_validation, validate_media_file,
)


def test_empty_file_is_invalid(tmp_path):
    p = tmp_path / "empty.mp3"
    p.touch()
    result = validate_media_file(p)
    assert not result.valid
    assert result.reason == "media file is empty"


def test_ffprobe_valid_audio(tmp_path):
    p = tmp_path / "ok.mp3"
    p.write_bytes(b"data")
    payload = {"format": {"format_name": "mp3", "duration": "12.5"}, "streams": [{"codec_type": "audio", "codec_name": "mp3"}]}
    with patch("worker.media_validation._run_ffprobe", return_value=payload):
        result = validate_media_file(p)
    assert result.valid and result.duration == 12.5 and result.codec == "mp3"


def test_missing_audio_stream_is_invalid(tmp_path):
    p = tmp_path / "video.mp4"; p.write_bytes(b"data")
    payload = {"format": {"format_name": "mov,mp4", "duration": "10"}, "streams": [{"codec_type": "video", "codec_name": "h264"}]}
    with patch("worker.media_validation._run_ffprobe", return_value=payload):
        result = validate_media_file(p)
    assert not result.valid and result.reason == "no audio stream found"


def test_video_requires_video_stream(tmp_path):
    p = tmp_path / "audio.mp4"; p.write_bytes(b"data")
    payload = {"format": {"format_name": "mov,mp4", "duration": "10"}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
    with patch("worker.media_validation._run_ffprobe", return_value=payload):
        result = validate_media_file(p, "video")
    assert not result.valid and result.reason == "no video stream found"


def test_ffprobe_missing_is_infrastructure_error(tmp_path):
    p = tmp_path / "x.mp3"; p.write_bytes(b"data")
    with patch("worker.media_validation.shutil.which", return_value=None):
        result = validate_media_file(p)
    assert not result.valid and result.infrastructure_error


def test_validation_fingerprint_is_stable(tmp_path):
    p = tmp_path / "x.mp3"; p.write_bytes(b"123")
    size, mtime = file_fingerprint(p)
    assert size == 3 and mtime == p.stat().st_mtime_ns


def test_needs_validation_skips_unchanged_valid_file(tmp_path):
    p = tmp_path / "x.mp3"; p.write_bytes(b"123")
    size, mtime = file_fingerprint(p)
    row = {"media_validation_status": "valid", "media_validation_version": CURRENT_VALIDATION_VERSION,
           "file_path": str(p), "media_validation_size": size, "media_validation_mtime_ns": mtime}
    assert not needs_validation(row)


def test_needs_validation_detects_changed_file(tmp_path):
    p = tmp_path / "x.mp3"; p.write_bytes(b"123")
    size, mtime = file_fingerprint(p)
    p.write_bytes(b"123456")
    row = {"media_validation_status": "valid", "media_validation_version": CURRENT_VALIDATION_VERSION,
           "file_path": str(p), "media_validation_size": size, "media_validation_mtime_ns": mtime}
    assert needs_validation(row)


def test_needs_validation_detects_validator_upgrade(tmp_path):
    p = tmp_path / "x.mp3"; p.write_bytes(b"123")
    size, mtime = file_fingerprint(p)
    row = {"media_validation_status": "valid", "media_validation_version": CURRENT_VALIDATION_VERSION - 1,
           "file_path": str(p), "media_validation_size": size, "media_validation_mtime_ns": mtime}
    assert needs_validation(row)
