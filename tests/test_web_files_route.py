import unittest

from web.app import app


class FileRouteTests(unittest.TestCase):
    def test_file_route_uses_query_track_id(self):
        routes = [
            route for route in app.routes
            if getattr(route, "path", "") == "/api/files"
            and any(param.name == "track_id" for param in route.dependant.query_params)
        ]
        self.assertEqual(len(routes), 1)
