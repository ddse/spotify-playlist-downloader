import unittest

from web.app import app


class FileRouteTests(unittest.TestCase):
    def test_file_route_accepts_encoded_track_id_with_slashes(self):
        routes = [
            route for route in app.routes
            if getattr(route, "path", "") == "/api/files/{track_id:path}"
        ]
        self.assertEqual(len(routes), 1)
