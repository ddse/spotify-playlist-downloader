import asyncio
import threading
from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest

from worker import search as worker_search
from web import youtube as web_youtube
from web import app as web_app


class FakeProvider:
    def __init__(self):
        self.calls = []

    def __call__(self, query, page, limit, **kwargs):
        self.calls.append((query, page, limit, kwargs))
        start = (page - 1) * limit
        items = [
            {
                "id": f"video-{i}",
                "title": f"{query} #{i}",
                "channel": "integration-channel",
                "duration": 120,
                "url": f"https://www.youtube.com/watch?v=video-{i}",
                "thumbnail": f"https://i.ytimg.com/vi/video-{i}/hqdefault.jpg",
                "source": "youtube",
            }
            for i in range(50)
        ]
        return {
            "items": items[start:start + limit],
            "page": page,
            "limit": limit,
            "has_more": start + limit < len(items),
        }


@contextmanager
def worker_server(provider):
    original = worker_search.PROVIDERS.get("youtube")
    worker_search.PROVIDERS["youtube"] = provider
    server = worker_search.ThreadingHTTPServer(("127.0.0.1", 0), worker_search.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if original is None:
            worker_search.PROVIDERS.pop("youtube", None)
        else:
            worker_search.PROVIDERS["youtube"] = original


@pytest.fixture
def worker_endpoint():
    provider = FakeProvider()
    with worker_server(provider) as endpoint:
        with patch.object(web_youtube, "WORKER_ENDPOINT", endpoint):
            yield provider


def request(path, **params):
    return httpx.get(path, params=params, timeout=5)


def test_search_integration_happy_path_and_pagination(worker_endpoint):
    provider = worker_endpoint

    first = request(
        f"{web_youtube.WORKER_ENDPOINT}/api/search/youtube",
        q="test song", page=1, limit=3, source="youtube", wireguard="0",
    )
    assert first.status_code == 200
    payload = first.json()
    assert [item["id"] for item in payload["items"]] == ["video-0", "video-1", "video-2"]
    assert payload["page"] == 1
    assert payload["limit"] == 3
    assert payload["has_more"] is True
    assert provider.calls[-1] == ("test song", 1, 3, {})

    second = request(
        f"{web_youtube.WORKER_ENDPOINT}/api/search/youtube",
        q="test song", page=2, limit=3, source="youtube", wireguard="0",
    )
    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == ["video-3", "video-4", "video-5"]


def test_search_integration_empty_query_does_not_call_provider(worker_endpoint):
    provider = worker_endpoint
    response = request(
        f"{web_youtube.WORKER_ENDPOINT}/api/search/youtube",
        q="   ", page=1, limit=10, source="youtube", wireguard="0",
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert provider.calls == []


def test_search_integration_limit_is_clamped(worker_endpoint):
    provider = worker_endpoint
    response = request(
        f"{web_youtube.WORKER_ENDPOINT}/api/search/youtube",
        q="song", page=1, limit=999, source="youtube", wireguard="0",
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 10
    assert len(payload["items"]) == 10
    assert provider.calls[-1][2] == 10


@pytest.mark.parametrize("wireguard", ["0", "1"])
def test_search_integration_preserves_wireguard_policy(worker_endpoint, wireguard):
    provider = worker_endpoint
    with patch.object(worker_search.manager, "setting_enabled", return_value=False),          patch.object(worker_search.manager, "debug_status", return_value={"status": "disconnected"}):
        response = request(
            f"{web_youtube.WORKER_ENDPOINT}/api/search/youtube",
            q="song", page=1, limit=2, source="youtube", wireguard=wireguard,
        )

    assert response.status_code == 200
    assert response.json()["items"]
    assert provider.calls[-1][0] == "song"


def test_search_integration_debug_contains_end_to_end_trace(worker_endpoint):
    response = request(
        f"{web_youtube.WORKER_ENDPOINT}/api/search/youtube",
        q="debug song", page=1, limit=2, source="youtube", wireguard="0", debug="1",
    )
    assert response.status_code == 200
    debug = response.json()["debug"]
    assert debug["request_received"] is True
    assert debug["source"] == "youtube"
    assert debug["query"] == "debug song"
    assert debug["wireguard_used"] is False
    assert [step["step"] for step in debug["steps"]] == [
        "wireguard_policy", "worker_received", "wireguard_before",
        "wireguard_after", "provider_search",
    ]


def test_search_integration_unknown_source_is_structured_error(worker_endpoint):
    response = request(
        f"{web_youtube.WORKER_ENDPOINT}/api/search/youtube",
        q="song", page=1, limit=10, source="does-not-exist", wireguard="0",
    )
    assert response.status_code == 500
    payload = response.json()
    assert payload["items"] == []
    assert "unsupported search provider" in payload["error"]


def test_search_integration_provider_failure_is_structured_error(worker_endpoint):
    def failing_provider(query, page, limit):
        raise RuntimeError("provider unavailable")

    worker_search.PROVIDERS["youtube"] = failing_provider
    response = request(
        f"{web_youtube.WORKER_ENDPOINT}/api/search/youtube",
        q="song", page=1, limit=10, source="youtube", wireguard="0", debug="1",
    )
    assert response.status_code == 500
    payload = response.json()
    assert payload["items"] == []
    assert "provider unavailable" in payload["error"]
    assert payload["debug"]["steps"][-1]["step"] == "worker_error"


def test_web_to_worker_search_client_integration(worker_endpoint):
    async def run():
        return await web_youtube.search(
            "  integration song  ", page=2, limit=4,
            source="youtube", wireguard=False, debug=True,
        )

    payload = asyncio.run(run())
    assert payload["page"] == 2
    assert payload["limit"] == 4
    assert payload["items"][0]["id"] == "video-4"
    assert payload["debug"]["web_to_worker"]["status"] == "ok"
    assert payload["debug"]["web_to_worker"]["http_status"] == 200


def test_web_search_endpoint_honors_wireguard_query_and_debug_error():
    async def failing_search(query, page, limit, source="youtube", wireguard=False, debug=False):
        assert query == "faded"
        assert page == 1
        assert limit == 10
        assert wireguard is False
        assert debug is True
        raise RuntimeError("provider unavailable")

    async def run():
        with patch.object(web_app, "youtube_search", failing_search):
            return await web_app.search_youtube(q="faded", page=1, limit=10, wireguard=0, debug=1)

    payload = asyncio.run(run())
    assert payload["items"] == []
    assert payload["error"] == "provider unavailable"
    assert payload["debug"]["wireguard_requested"] is False
    assert payload["debug"]["wireguard_used"] is False
    assert payload["debug"]["steps"][-1]["step"] == "web_search_error"


def test_web_to_worker_search_non_debug_raises_on_worker_failure(worker_endpoint):
    worker_search.PROVIDERS["youtube"] = lambda query, page, limit: (
        (_ for _ in ()).throw(RuntimeError("boom"))
    )

    async def run():
        await web_youtube.search(
            "song", page=1, limit=10, source="youtube",
            wireguard=False, debug=False,
        )

    with pytest.raises(Exception):
        asyncio.run(run())


def test_worker_route_rejects_unknown_path(worker_endpoint):
    response = request(f"{web_youtube.WORKER_ENDPOINT}/api/search/unknown", q="song")
    assert response.status_code == 404
    assert response.json() == {"error": "not found"}
