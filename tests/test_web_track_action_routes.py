import unittest

from web.app import app


class TrackActionRouteTests(unittest.TestCase):
    def test_track_actions_do_not_put_track_id_in_path(self):
        expected = {
            "/api/files", "/api/retry", "/api/queue/start",
            "/api/queue/pause", "/api/queue/prioritize", "/api/queue",
        }
        paths = {route.path for route in app.routes}
        self.assertTrue(expected.issubset(paths))

    def test_track_action_routes_have_query_track_id(self):
        for route in app.routes:
            if route.path in {
                "/api/files", "/api/retry", "/api/queue/start",
                "/api/queue/pause", "/api/queue/prioritize", "/api/queue"
            }:
                self.assertIn("track_id", {param.name for param in route.dependant.query_params})
