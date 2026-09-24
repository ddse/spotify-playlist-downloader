import unittest

from web.app import app


class RetryRouteTests(unittest.TestCase):
    def test_retry_route_accepts_encoded_track_id_with_slashes(self):
        routes = [route for route in app.routes if getattr(route, "path", "") == "/api/retry/{track_id:path}"]
        self.assertEqual(len(routes), 1)
