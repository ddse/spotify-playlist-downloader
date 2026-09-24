import unittest

from web.app import app


class RetryRouteTests(unittest.TestCase):
    def test_retry_route_uses_query_track_id(self):
        routes = [
            route for route in app.routes
            if getattr(route, "path", "") == "/api/retry"
            and any(param.name == "track_id" for param in route.dependant.query_params)
        ]
        self.assertEqual(len(routes), 1)
