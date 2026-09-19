import importlib.util
import os
import pathlib

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_provider(name):
    spec = importlib.util.spec_from_file_location(
        f"wireguard_live_provider_{name}",
        ROOT / "worker" / "providers" / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.wireguard_live
def test_real_provider_search_runs_through_wireguard():
    if os.getenv("RUN_WIREGUARD_LIVE") != "1":
        pytest.skip("set RUN_WIREGUARD_LIVE=1 to run the real WireGuard test")

    from worker import wireguard

    previous_enabled = wireguard.is_up()
    if not pathlib.Path(wireguard.CONFIG).is_file():
        pytest.fail(f"WireGuard config not found: {wireguard.CONFIG}")

    def exercise_providers():
        status = wireguard.status()
        assert status["enabled"], status
        assert status["route_active"], status
        assert status["handshake_recent"], status
        assert status["vpn_route"], status
        assert status["public_ip"], status

        zing = load_provider("zingmp3")
        nct = load_provider("nhaccuatui")

        zing_result = zing.search("Đừng Làm Trái Tim Anh Đau", page=1, limit=5)
        assert zing_result["items"], "Zing MP3 returned no real results over WireGuard"
        assert all(item["url"].startswith("https://zingmp3.vn/") for item in zing_result["items"])

        nct_result = nct.search("Đừng Làm Trái Tim Anh Đau", page=1, limit=5)
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
        wireguard.run(True, exercise_providers)
    finally:
        if not previous_enabled:
            wireguard.set_enabled(False)
