import sqlite3
import unittest

from worker.progress import clear_download_progress


class DownloadProgressTests(unittest.TestCase):
    def test_clear_download_progress_parameterizes_empty_stats(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE tracks (
                spotify_id TEXT PRIMARY KEY,
                progress INTEGER,
                download_speed TEXT,
                eta TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO tracks VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            ("track-1", 87, "1.2 MB/s", "3s"),
        )

        clear_download_progress(conn, "track-1")
        conn.commit()

        row = conn.execute(
            "SELECT progress, download_speed, eta FROM tracks WHERE spotify_id=?",
            ("track-1",),
        ).fetchone()
        conn.close()

        self.assertEqual(row, (99, "", ""))


if __name__ == "__main__":
    unittest.main()
