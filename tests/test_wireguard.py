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
        self.config.write_text(
            "[Interface]\nPrivateKey = test\nAddress = 10.0.0.2/24\n"
        )
        self.old_config = wireguard.CONFIG
        wireguard.CONFIG = str(self.config)
        wireguard._state = False

    def tearDown(self):
        self.wireguard.CONFIG = self.old_config
        self.wireguard._state = False
        self.tmp.cleanup()


    def test_runtime_config_name_matches_interface_for_wg_quick(self):
        self.assertEqual(Path(self.wireguard.RUNTIME_CONFIG).name, f"{self.wireguard.INTERFACE}.conf")

    def test_database_config_is_used_and_materialized_without_exposing_contents(self):
        original = self.wireguard._db_config
        self.wireguard._db_config = lambda: "[Interface]\nPrivateKey = secret\nAddress = 10.0.0.2/24"
        try:
            self.assertTrue(self.wireguard.config_configured())
            with patch.object(self.wireguard.os, "chmod") as chmod:
                path = self.wireguard._materialize_config()
            self.assertEqual(Path(path).read_text(), "[Interface]\nPrivateKey = secret\nAddress = 10.0.0.2/24\n")
            chmod.assert_called_once_with(path, 0o600)
            self.assertEqual(self.wireguard.config_content().splitlines()[1], "PrivateKey = secret")
        finally:
            self.wireguard._db_config = original
            try:
                os.remove(self.wireguard.RUNTIME_CONFIG)
            except OSError:
                pass

    def test_status_reports_database_as_config_source(self):
        with patch.object(self.wireguard, "is_up", return_value=False), \
             patch.object(self.wireguard, "_db_config", return_value="[Interface]\nPrivateKey = secret"), \
             patch.object(self.wireguard, "_legacy_config", return_value=""):
            result = self.wireguard.status()
        self.assertTrue(result["config_exists"])
        self.assertEqual(result["config_source"], "database")
        self.assertNotIn("PrivateKey = secret", str(result))

    def test_is_up_returns_false_when_wg_binary_is_missing(self):
        with patch.object(
            self.wireguard.subprocess,
            "run",
            side_effect=FileNotFoundError(2, "No such file or directory", "wg"),
        ):
            self.assertFalse(self.wireguard.is_up())

    def test_setting_enabled_reads_persisted_preference(self):
        self.wireguard._state = False
        original_db = self.wireguard.db if hasattr(self.wireguard, "db") else None
        # The module reads the shared app_settings table; a missing row falls back to runtime state.
        self.assertFalse(self.wireguard.setting_enabled())

    def test_set_enabled_persists_preference(self):
        with patch.object(self.wireguard, "_persist_enabled") as persist,              patch.object(self.wireguard, "_run") as run,              patch.object(self.wireguard, "is_up", side_effect=[True, True]):
                self.wireguard.set_enabled(True)
                self.wireguard.set_enabled(False)
        persist.assert_any_call(True)
        persist.assert_any_call(False)

    def test_restore_persisted_state_starts_wireguard(self):
        with patch.object(self.wireguard, "setting_enabled", return_value=True),              patch.object(self.wireguard, "set_enabled") as set_enabled:
            self.assertTrue(self.wireguard.restore_persisted_state())
            import time
            for _ in range(20):
                if set_enabled.called:
                    break
                time.sleep(0.01)
            set_enabled.assert_called_once_with(True)

    def test_run_does_not_toggle_global_interface(self):
        with patch.object(self.wireguard, "set_enabled") as set_enabled:
            result = self.wireguard.run(True, lambda: "ok")
        self.assertEqual(result, "ok")
        set_enabled.assert_not_called()

    def test_run_download_does_not_toggle_global_interface(self):
        with patch.object(self.wireguard, "set_enabled") as set_enabled:
            result = self.wireguard.run_download(True, lambda: "ok")
        self.assertEqual(result, "ok")
        set_enabled.assert_not_called()

    def test_status_config_exists_when_live_interface_has_no_config_file(self):
        with patch.object(self.wireguard, "is_up", return_value=True),              patch.object(self.wireguard.os.path, "isfile", return_value=False),              patch.object(self.wireguard, "_route_status", return_value=(False, [])),              patch.object(self.wireguard, "_handshake_status", return_value=(False, [])),              patch.object(self.wireguard, "_public_ip", return_value=""):
            result = self.wireguard.status()
        self.assertTrue(result["enabled"])
        self.assertTrue(result["config_exists"])
        self.assertEqual(result["config_source"], "live_interface")

    def test_debug_status_handles_status_exception_without_undefined_variable(self):
        with patch.object(self.wireguard, "status", side_effect=RuntimeError("boom")),              patch.object(self.wireguard, "is_up", return_value=True):
            result = self.wireguard.debug_status()
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["config_exists"])
        self.assertFalse(result["interface_up"])

    def test_apply_enabled_async_rejects_duplicate_transition(self):
        self.wireguard._operation = "connecting"
        self.assertFalse(self.wireguard.apply_enabled_async(False))
        self.wireguard._operation = None

    def test_enable_runs_wg_quick_up_only_when_down(self):
        with patch.object(
            self.wireguard, "is_up", side_effect=[False, True]
        ), patch.object(self.wireguard, "_run") as run:
            self.wireguard.set_enabled(True)
            self.wireguard.set_enabled(True)

        run.assert_called_once_with("wg-quick", "up", self.wireguard.RUNTIME_CONFIG)

    def test_disable_runs_wg_quick_down_only_when_up(self):
        with patch.object(
            self.wireguard, "is_up", return_value=True
        ), patch.object(self.wireguard, "_run") as run:
            self.wireguard.set_enabled(False)

        run.assert_called_once_with("wg-quick", "down", self.wireguard.INTERFACE)

    def test_enable_fails_when_config_is_missing(self):
        self.wireguard.CONFIG = str(Path(self.tmp.name) / "missing.conf")

        with self.assertRaisesRegex(RuntimeError, "WireGuard configuration is not saved in the database"):
            self.wireguard.set_enabled(True)

    def test_route_status_detects_wg_quick_policy_route(self):
        result = type("Result", (), {
            "stdout": (
                "default via 172.18.0.1 dev eth0\n"
                "default dev wg0 table 51820 proto static\n"
                "10.0.0.0/24 dev wg0 proto kernel scope link src 10.0.0.2\n"
            )
        })()

        with patch.object(
            self.wireguard.subprocess, "run", return_value=result
        ) as run:
            active, routes = self.wireguard._route_status()

        self.assertTrue(active)
        self.assertEqual(routes, [
            "default via 172.18.0.1 dev eth0",
            "default dev wg0 table 51820 proto static",
        ])
        run.assert_called_once_with(
            ["ip", "-4", "route", "show", "table", "all"],
            check=True,
            stdout=self.wireguard.subprocess.PIPE,
            stderr=self.wireguard.subprocess.PIPE,
            text=True,
        )

    def test_route_status_ignores_default_route_on_other_interface(self):
        result = type("Result", (), {
            "stdout": (
                "default via 172.18.0.1 dev eth0\n"
                "192.168.1.0/24 dev eth0 proto kernel scope link\n"
            )
        })()

        with patch.object(
            self.wireguard.subprocess, "run", return_value=result
        ):
            active, routes = self.wireguard._route_status()

        self.assertFalse(active)
        self.assertEqual(routes, ["default via 172.18.0.1 dev eth0"])

    def test_route_status_handles_ip_command_failure(self):
        with patch.object(
            self.wireguard.subprocess,
            "run",
            side_effect=self.wireguard.subprocess.CalledProcessError(
                1, ["ip", "-4", "route", "show", "table", "all"]
            ),
        ):
            active, routes = self.wireguard._route_status()

        self.assertFalse(active)
        self.assertEqual(routes, [])

    def test_status_reports_active_vpn_when_route_handshake_and_public_ip_are_valid(self):
        with patch.object(self.wireguard, "is_up", return_value=True), patch.object(
            self.wireguard,
            "_route_status",
            return_value=(True, ["default dev wg0"]),
        ), patch.object(
            self.wireguard,
            "_handshake_status",
            return_value=(True, [{"public_key": "peer", "age_seconds": 5}]),
        ), patch.object(
            self.wireguard, "_public_ip", return_value="203.0.113.10"
        ), patch.object(
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

    def test_status_is_not_vpn_active_without_recent_handshake(self):
        with patch.object(self.wireguard, "is_up", return_value=True), patch.object(
            self.wireguard,
            "_route_status",
            return_value=(True, ["default dev wg0"]),
        ), patch.object(
            self.wireguard,
            "_handshake_status",
            return_value=(False, [{"public_key": "peer", "age_seconds": None}]),
        ), patch.object(
            self.wireguard, "_public_ip", return_value="203.0.113.10"
        ), patch.object(
            self.wireguard.subprocess,
            "run",
            return_value=type("Result", (), {"stdout": ""})(),
        ):
            result = self.wireguard.status()

        self.assertTrue(result["enabled"])
        self.assertTrue(result["route_active"])
        self.assertFalse(result["handshake_recent"])
        self.assertFalse(result["vpn_route"])
        self.assertEqual(result["status"], "connecting")
        self.assertIn("waiting for a recent peer handshake", result["status_detail"])

    def test_status_reports_disconnected_when_interface_is_down(self):
        with patch.object(self.wireguard, "is_up", return_value=False):
            result = self.wireguard.status()

        self.assertFalse(result["enabled"])
        self.assertEqual(result["status"], "disconnected")
        self.assertIn("interface is down", result["status_detail"])


class WorkerApiHandlerTests(unittest.TestCase):
    def test_json_ignores_client_disconnect_without_retrying_response(self):
        from worker.search import Handler

        class BrokenWriter:
            def write(self, body):
                raise BrokenPipeError(32, "Broken pipe")

        handler = Handler.__new__(Handler)
        handler.wfile = BrokenWriter()
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None

        self.assertFalse(handler._json(200, {"ok": True}))


if __name__ == "__main__":
    unittest.main()
