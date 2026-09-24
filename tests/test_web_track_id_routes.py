import unittest

from web.app import app


TRACK_ACTION_PATHS = {
    "/api/queue/start",
    "/api/queue/pause",
    "/api/queue/prioritize",
    "/api/files",
    "/api/retry",
    "/api/queue",
}


class TrackIdRouteTests(unittest.TestCase):
    def test_all_track_id_routes_use_query_parameter(self):
        action_routes = {
            route.path: route
            for route in app.routes
            if getattr(route, "path", "") in TRACK_ACTION_PATHS
            and any(param.name == "track_id" for param in route.dependant.query_params)
        }
        self.assertTrue(TRACK_ACTION_PATHS.issubset(action_routes))

    def test_track_id_routes_do_not_use_path_converter(self):
        paths = {route.path for route in app.routes}
        self.assertFalse(any("{track_id:path}" in path for path in paths))

    def test_retry_route_is_registered_once(self):
        routes = [
            route for route in app.routes
            if getattr(route, "path", "") == "/api/retry"
            and "POST" in (getattr(route, "methods", set()) or set())
        ]
        self.assertEqual(len(routes), 1)
