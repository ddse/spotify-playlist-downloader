from pathlib import Path


FRONTEND_APP = Path(__file__).parents[1] / "web" / "frontend" / "src" / "App.jsx"


def test_service_status_badges_tolerate_partial_api_payloads():
    source = FRONTEND_APP.read_text(encoding="utf-8")

    # /api/services can temporarily contain only a subset of service state while
    # the backend/worker is restarting. Rendering must not dereference a missing
    # service object.
    assert "data.services.worker?.status" in source
    assert "data.services.scheduler?.status" in source
