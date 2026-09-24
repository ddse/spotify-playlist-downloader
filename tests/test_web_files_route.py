import unittest

from web.app import app


class FileRouteTests(unittest.TestCase):
    def test_file_download_route_uses_query_track_id(self):
        routes = [
            route for route in app.routes
            if getattr(route, "path", "") == "/api/files"
            and "GET" in (getattr(route, "methods", set()) or set())
            and any(param.name == "track_id" for param in route.dependant.query_params)
        ]
        self.assertEqual(len(routes), 1)

    def test_file_delete_route_uses_query_track_id(self):
        routes = [
            route for route in app.routes
            if getattr(route, "path", "") == "/api/files"
            and "DELETE" in (getattr(route, "methods", set()) or set())
            and any(param.name == "track_id" for param in route.dependant.query_params)
        ]
        self.assertEqual(len(routes), 1)
