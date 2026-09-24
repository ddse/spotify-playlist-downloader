import unittest

from web.app import app


class TrackIdRouteTests(unittest.TestCase):
    def test_all_track_id_routes_use_path_converter(self):
        expected = {
            "/api/queue/{track_id:path}/start",
            "/api/queue/{track_id:path}/pause",
            "/api/queue/prioritize/{track_id:path}",
            "/api/files/{track_id:path}",
            "/api/retry/{track_id:path}",
            "/api/queue/{track_id:path}",
        }
        paths = {route.path for route in app.routes if "{track_id" in getattr(route, "path", "")}
        self.assertTrue(expected.issubset(paths))

    def test_retry_route_is_registered_once(self):
        routes = [
            route for route in app.routes
            if getattr(route, "path", "") == "/api/retry/{track_id:path}"
        ]
        self.assertEqual(len(routes), 1)
