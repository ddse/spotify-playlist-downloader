import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DatabaseMigrationTests(unittest.TestCase):
    def test_wireguard_column_is_added_to_legacy_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "app.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE tracks (
                    spotify_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    artists TEXT NOT NULL
                )
                """
            )
            conn.commit()
            conn.close()

            old_db_path = os.environ.get("DB_PATH")
            os.environ["DB_PATH"] = db_path
            try:
                import importlib
                import database
                importlib.reload(database)

                database.init_db()

                conn = sqlite3.connect(db_path)
                columns = {
                    row[1]: row[4]
                    for row in conn.execute("PRAGMA table_info(tracks)")
                }
                row = conn.execute(
                    "SELECT wireguard FROM tracks WHERE spotify_id=?",
                    ("missing",),
                ).fetchone()
                conn.close()

                self.assertIn("wireguard", columns)
                self.assertEqual(columns["wireguard"], "0")
                self.assertIsNone(row)
            finally:
                if old_db_path is None:
                    os.environ.pop("DB_PATH", None)
                else:
                    os.environ["DB_PATH"] = old_db_path


class WireGuardManagerTests(unittest.TestCase):
    def setUp(self):
        from worker import wireguard
        self.wireguard = wireguard
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "wg0.conf"
        self.config.write_text("[Interface]\nPrivateKey = test\nAddress = 10.0.0.2/24\n")
        self.old_config = wireguard.CONFIG
        wireguard.CONFIG = str(self.config)
        wireguard._state = False

    def tearDown(self):
        self.wireguard.CONFIG = self.old_config
        self.wireguard._state = False
        self.tmp.cleanup()

    def test_enable_runs_wg_quick_up_only_when_down(self):
        with patch.object(self.wireguard, "is_up", side_effect=[False, True]),              patch.object(self.wireguard, "_run") as run:
            self.wireguard.set_enabled(True)
            self.wireguard.set_enabled(True)

        run.assert_called_once_with("wg-quick", "up", str(self.config))

    def test_disable_runs_wg_quick_down_only_when_up(self):
        with patch.object(self.wireguard, "is_up", return_value=True),              patch.object(self.wireguard, "_run") as run:
            self.wireguard.set_enabled(False)

        run.assert_called_once_with("wg-quick", "down", str(self.config))

    def test_enable_fails_when_config_is_missing(self):
        self.wireguard.CONFIG = str(Path(self.tmp.name) / "missing.conf")

        with self.assertRaisesRegex(RuntimeError, "WireGuard config not found"):
            self.wireguard.set_enabled(True)

    def test_status_reports_active_vpn_when_route_handshake_and_public_ip_are_valid(self):
        with patch.object(self.wireguard, "is_up", return_value=True),              patch.object(
                 self.wireguard,
                 "_route_status",
                 return_value=(True, ["default dev wg0"]),
             ),              patch.object(
                 self.wireguard,
                 "_handshake_status",
                 return_value=(True, [{"public_key": "peer", "age_seconds": 5}]),
             ),              patch.object(self.wireguard, "_public_ip", return_value="203.0.113.10"),              patch.object(
                 self.wireguard.subprocess,
                 "run",
                 return_value=type(
                     "Result",
                     (),
                     {"stdout": "peer 1024 2048\n"},
                 )(),
             ):
            result = self.wireguard.status()

        self.assertTrue(result["enabled"])
        self.assertTrue(result["route_active"])
        self.assertTrue(result["handshake_recent"])
        self.assertTrue(result["vpn_route"])
        self.assertEqual(result["public_ip"], "203.0.113.10")
        self.assertEqual(result["receive_bytes"], 1024)
        self.assertEqual(result["send_bytes"], 2048)

    def test_route_status_detects_wg_quick_policy_route(self):
        class Result:
            def __init__(self, stdout):
                self.stdout = stdout

        def run(*args, **kwargs):
            command = list(args)
            if command[:5] == ["ip", "-4", "route", "show", "table"]:
                return Result("default dev wg0 table 51820\n")
            if command[:4] == ["ip", "-4", "rule"]:
                return Result("32764: from all lookup main suppress_prefixlength 0\n")
            raise AssertionError(f"unexpected command: {command}")

        with patch.object(self.wireguard.subprocess, "run", side_effect=run):
            active, routes = self.wireguard._route_status()

        self.assertTrue(active)
        self.assertEqual(routes, ["default dev wg0 table 51820"])

    def test_status_is_not_vpn_active_without_recent_handshake(self):
        with patch.object(self.wireguard, "is_up", return_value=True),              patch.object(
                 self.wireguard,
                 "_route_status",
                 return_value=(True, ["default dev wg0"]),
             ),              patch.object(
                 self.wireguard,
                 "_handshake_status",
                 return_value=(False, [{"public_key": "peer", "age_seconds": None}]),
             ),              patch.object(self.wireguard, "_public_ip", return_value="203.0.113.10"),              patch.object(
                 self.wireguard.subprocess,
                 "run",
                 return_value=type("Result", (), {"stdout": ""})(),
             ):
            result = self.wireguard.status()

        self.assertTrue(result["enabled"])
        self.assertTrue(result["route_active"])
        self.assertFalse(result["handshake_recent"])
        self.assertFalse(result["vpn_route"])


if __name__ == "__main__":
    unittest.main()
