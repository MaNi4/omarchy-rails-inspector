import http.client
import json
import threading
import unittest

import helper
from rails_inspector.browser import local_host_header, local_origin, make_server, record_report
from rails_inspector.sourcemaps import SourceMaps
from rails_inspector.store import ProblemStore


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.root = helper.project_dir()
        self.store = ProblemStore(self.root)

    def report(self, **fields):
        return record_report(self.store, SourceMaps(), self.root, fields)

    def test_levels_and_labels(self):
        p = self.report(type="warning", level="warning", message="careful", page="/")
        self.assertEqual(p["level"], "warning")
        self.assertEqual(p["sub"], "console.warn · /")

    def test_ids_in_urls_merge(self):
        a = self.report(type="http", message="POST /posts/123 → 500", source="http://localhost:3000/posts/123")
        b = self.report(type="http", message="POST /posts/456 → 500", source="http://localhost:3000/posts/456")
        self.assertIs(a, b)
        self.assertEqual(a["where"], "")

    def test_garbage_does_not_raise(self):
        p = self.report(type="error", message=None, line="abc", col={}, stack=None, source=None)
        self.assertEqual(p["title"], "Uncaught error")

    def test_outside_location_is_not_openable(self):
        p = self.report(type="error", message="x", source="http://evil/etc/passwd", line=1)
        self.assertFalse(p["openable"])


class OriginTest(unittest.TestCase):
    def test_origins(self):
        self.assertTrue(local_origin(None))
        self.assertTrue(local_origin("http://localhost:3000"))
        self.assertTrue(local_origin("http://127.0.0.1:3001"))
        self.assertTrue(local_origin("http://shop.localhost:3000"))
        self.assertTrue(local_origin("http://dev.example:3000", ["http://dev.example:3000"]))
        self.assertFalse(local_origin("https://evil.example"))
        self.assertFalse(local_origin("http://localhost.evil.example"))

    def test_host_header(self):
        self.assertTrue(local_host_header("127.0.0.1:9787", 9787))
        self.assertTrue(local_host_header("localhost:9787", 9787))
        self.assertTrue(local_host_header("[::1]:9787", 9787))
        self.assertFalse(local_host_header("evil.example:9787", 9787))
        self.assertFalse(local_host_header("127.0.0.1:9788", 9787))
        self.assertFalse(local_host_header(None, 9787))


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.reports = []
        self.server = make_server(0, self.reports.append, lambda: [])
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def host(self, origin=None):
        h = {"Host": "127.0.0.1:%d" % self.port, "Content-Type": "text/plain"}
        if origin:
            h["Origin"] = origin
        return h

    def test_snippet_carries_project_and_port(self):
        status, body = self.request("GET", "/rails-inspector.js?project=shop", headers=self.host())
        self.assertEqual(status, 200)
        self.assertIn(b'var P = "shop"', body)
        self.assertIn(("127.0.0.1:%d/report" % self.port).encode(), body)

    def test_report_from_local_page(self):
        body = json.dumps({"project": "shop", "message": "x"})
        status, _ = self.request("POST", "/report", body, self.host("http://localhost:3000"))
        self.assertEqual(status, 204)
        self.assertEqual(self.reports, [{"project": "shop", "message": "x"}])

    def test_report_from_foreign_page_is_refused(self):
        status, _ = self.request("POST", "/report", "{}", self.host("https://evil.example"))
        self.assertEqual(status, 403)
        self.assertEqual(self.reports, [])

    def test_rebound_host_is_refused(self):
        h = self.host("http://localhost:3000")
        h["Host"] = "evil.example:%d" % self.port
        self.assertEqual(self.request("POST", "/report", "{}", h)[0], 403)
        self.assertEqual(self.request("GET", "/rails-inspector.js", headers=h)[0], 403)

    def test_bad_bodies(self):
        self.assertEqual(self.request("POST", "/report", "", self.host())[0], 403)
        self.assertEqual(self.request("POST", "/report", "not json", self.host())[0], 204)
        self.assertEqual(self.request("POST", "/report", "[1]", self.host())[0], 204)
        self.assertEqual(self.reports, [])
        self.assertEqual(self.request("GET", "/other", headers=self.host())[0], 404)


if __name__ == "__main__":
    unittest.main()
