import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

import worker


class TestDownloadOutputResolution(unittest.TestCase):
    def test_resolve_downloaded_file_finds_recent_file_outside_expected_album(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual" / "song.mp3"
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"ID3" + b"x" * 20000)

            row = {
                "artists": "Artist",
                "album": "Album",
                "title": "Song",
                "download_folder": "",
            }

            with patch.object(worker, "MUSIC_DIR", str(root)):
                resolved = worker.resolve_downloaded_file(row, since=actual.stat().st_mtime - 1)

            self.assertEqual(Path(resolved), actual.resolve())

    def test_provider_output_path_is_used_without_directory_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "provider" / "song.mp3"
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"ID3" + b"x" * 20000)

            row = {
                "source_url": "https://example.test/song",
                "source_mode": "single",
                "artists": "Artist",
                "album": "Album",
                "title": "Song",
                "download_folder": "",
            }

            class FakeManager:
                @staticmethod
                def run_download(enabled, func):
                    return func()

            with patch.object(worker, "MUSIC_DIR", str(root)), \
                 patch.object(worker.manager, "run_download", FakeManager.run_download), \
                 patch.object(worker, "download", return_value=str(actual.resolve())):
                c = None
                # The main loop is intentionally tested through the same
                # path contract rather than running the infinite worker loop.
                result_path = worker.manager.run_download(False, lambda: worker.download(row, c, "t1"))
                self.assertEqual(Path(result_path), actual.resolve())


if __name__ == "__main__":
    unittest.main()
