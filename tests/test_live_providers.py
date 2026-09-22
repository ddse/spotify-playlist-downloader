import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_provider(name):
    spec = importlib.util.spec_from_file_location(
        f"live_provider_{name}",
        ROOT / "worker" / "providers" / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_real_result(item, domain, expected_title):
    assert item["source"] in {"zingmp3", "nhaccuatui"}
    assert item["title"].strip()
    assert expected_title.casefold() in item["title"].casefold()
    assert item["url"].startswith(f"https://{domain}/")
    assert item["id"]


@pytest.mark.live
def test_live_zingmp3_search_returns_real_result():
    zing = load_provider("zingmp3")
    result = zing.search("Đừng Làm Trái Tim Anh Đau", page=1, limit=10)

    assert result["page"] == 1
    assert result["limit"] == 10
    assert result["items"], "Zing MP3 production search returned no results"
    _assert_real_result(result["items"][0], "zingmp3.vn", "Đừng Làm Trái Tim Anh Đau")


@pytest.mark.live
def test_live_nhaccuatui_search_returns_real_result():
    nct = load_provider("nhaccuatui")
    result = nct.search("Đừng Làm Trái Tim Anh Đau", page=1, limit=10)

    assert result["page"] == 1
    assert result["limit"] == 10
    assert result["items"], "NhacCuaTui production search returned no results"

    item = next(
        item for item in result["items"]
        if "Đừng Làm Trái Tim Anh Đau".casefold() in item["title"].casefold()
    )
    _assert_real_result(item, "www.nhaccuatui.com", "Đừng Làm Trái Tim Anh Đau")
