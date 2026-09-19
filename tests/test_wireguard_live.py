import importlib.util
import os
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_wireguard():
    candidates = [ROOT / "worker" / "wireguard.py", ROOT / "wireguard.py"]
    for path in candidates:
        if path.is_file():
            worker_root = path.parent
            if str(worker_root) not in sys.path:
                sys.path.insert(0, str(worker_root))
            spec = importlib.util.spec_from_file_location("wireguard_live_manager", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    pytest.fail("WireGuard manager not found in source or Docker image")


def load_provider(name):
    candidates = [
        ROOT / "worker" / "providers" / f"{name}.py",
        ROOT / "providers" / f"{name}.py",
    ]
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location(
                f"wireguard_live_provider_{name}", path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    pytest.fail(f"Provider not found: {name}")


def _step(name):
    print(f"\\n[WireGuard live] {name}", flush=True)


@pytest.mark.wireguard_live
def test_real_provider_search_runs_through_wireguard():
    if os.getenv("RUN_WIREGUARD_LIVE") != "1":
        pytest.skip("set RUN_WIREGUARD_LIVE=1 to run the real WireGuard test")

    wireguard = _load_wireguard()

    _step("Checking WireGuard config")
    previous_enabled = wireguard.is_up()
    if not pathlib.Path(wireguard.CONFIG).is_file():
        pytest.fail(f"WireGuard config not found: {wireguard.CONFIG}")

    def exercise_providers():
        _step("Checking interface, route, handshake and public IP")
        status = wireguard.status()
        assert status["enabled"], status
        assert status["route_active"], status
        assert status["handshake_recent"], status
        assert status["vpn_route"], status
        assert status["public_ip"], status

        _step("Testing YouTube")
        youtube_result = load_provider("youtube").search(
            "Đừng Làm Trái Tim Anh Đau", page=1, limit=5
        )
        assert youtube_result["items"], "YouTube returned no real results over WireGuard"
        assert all(
            item["url"].startswith("https://www.youtube.com/")
            for item in youtube_result["items"]
        )

        _step("Testing Zing MP3")
        zing_result = load_provider("zingmp3").search(
            "Đừng Làm Trái Tim Anh Đau", page=1, limit=5
        )
        assert zing_result["items"], "Zing MP3 returned no real results over WireGuard"
        assert all(
            item["url"].startswith("https://zingmp3.vn/")
            for item in zing_result["items"]
        )

        _step("Testing NhacCuaTui")
        nct_result = load_provider("nhaccuatui").search(
            "Đừng Làm Trái Tim Anh Đau", page=1, limit=5
        )
        assert nct_result["items"], "NhacCuaTui returned no real results over WireGuard"
        assert any(
            "Đừng Làm Trái Tim Anh Đau".casefold() in item["title"].casefold()
            for item in nct_result["items"]
        )
        assert all(
            item["url"].startswith("https://www.nhaccuatui.com/")
            for item in nct_result["items"]
        )

    try:
        _step("Enabling WireGuard")
        wireguard.run(True, exercise_providers)
    finally:
        if not previous_enabled:
            _step("Restoring WireGuard to OFF")
            wireguard.set_enabled(False)
