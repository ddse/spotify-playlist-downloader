import os
import tempfile
import time
from pathlib import Path

from worker.output import describe_recent_media, resolve_downloaded_file


def row(**overrides):
    data = {
        'artists': 'Artist',
        'album': 'Album',
        'title': 'Song',
        'download_folder': '',
    }
    data.update(overrides)
    return data


def test_resolve_downloaded_file_finds_mp3_created_by_current_job():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / 'Artist' / 'Album'
        folder.mkdir(parents=True)
        path = folder / 'Song.mp3'
        path.write_bytes(b'0' * 4096)
        since = time.time() - 1
        assert resolve_downloaded_file(row(), root, since) == str(path.resolve())


def test_resolve_downloaded_file_accepts_postprocessed_extension():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / 'Artist' / 'Album'
        folder.mkdir(parents=True)
        path = folder / 'Song.m4a'
        path.write_bytes(b'1' * 4096)
        assert resolve_downloaded_file(row(), root, time.time() - 1).endswith('Song.m4a')


def test_resolve_downloaded_file_does_not_accept_stale_media():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / 'Artist' / 'Album'
        folder.mkdir(parents=True)
        path = folder / 'Song.mp3'
        path.write_bytes(b'2' * 4096)
        old = time.time() - 3600
        os.utime(path, (old, old))
        assert resolve_downloaded_file(row(), root, time.time() - 5) == ''


def test_resolve_downloaded_file_finds_sanitized_name():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / 'Artist' / 'Album'
        folder.mkdir(parents=True)
        path = folder / 'Song_-_Live.mp3'
        path.write_bytes(b'3' * 4096)
        # Exact stem normalization is intentionally strict; newest recent
        # media remains the fallback for yt-dlp sanitization.
        assert resolve_downloaded_file(row(), root, time.time() - 1) == str(path.resolve())


def test_describe_recent_media_reports_debug_context():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / 'missing.mp3'
        path.write_bytes(b'4' * 4096)
        recent = describe_recent_media(root, time.time() - 1)
        assert recent and recent[0]['path'] == str(path.resolve())
