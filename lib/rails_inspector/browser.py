import http.server
import json
import re
import socketserver
import urllib.parse

from .snippet import SNIPPET

LABELS = {"error": "Uncaught error", "rejection": "Unhandled rejection", "console": "console.error",
          "warning": "console.warn", "assert": "console.assert", "resource": "Failed to load",
          "http": "HTTP error", "network": "Network error", "csp": "CSP violation",
          "deprecation": "Browser deprecation", "intervention": "Browser intervention",
          "longtask": "Long task", "turbo": "Turbo"}
WITHOUT_LOCATION = ("http", "resource", "network")
NOTIFYING = ("error", "rejection", "console", "http", "csp")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
MAX_BODY = 65536


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def record_report(store, maps, project_path, report):
    """One report of the page's reporter as a problem in the JavaScript tab."""
    kind_of = str(report.get("type") or "error")[:32]
    level = "warning" if report.get("level") == "warning" else "error"
    msg = str(report.get("message") or "")[:2000]
    source = str(report.get("source") or "")[:2000]
    page = str(report.get("page") or "")[:500]
    stack = str(report.get("stack") or "")[:4000]
    line, col = to_int(report.get("line")), to_int(report.get("col"))
    where = "" if kind_of in WITHOUT_LOCATION else maps.locate(project_path, source, line, col, stack)
    stack = maps.translate_stack(project_path, stack)
    label = LABELS.get(kind_of, kind_of)
    key = re.sub(r"\d{4,}|\b\d+ms\b", "#", re.sub(r"/\d+", "/#", msg))[:300]
    detail = "\n".join(filter(None, [label + ": " + msg, page and "Page: " + page, stack]))
    return store.record("js", [kind_of, key, where], msg.splitlines()[0][:200] if msg else label,
                        "%s · %s" % (label, page) if page else label, where, detail,
                        notify=level == "error" and kind_of in NOTIFYING, level=level)


def local_origin(origin, extra=()):
    """Whether a page at this origin may report: a local development host, or one listed in jsOrigins."""
    if not origin:
        return True
    if origin in extra:
        return True
    host = urllib.parse.urlparse(origin).hostname or ""
    return host in LOCAL_HOSTS or host.endswith(".localhost")


HOST_HEADER = re.compile(r"^(\[[^\]]+\]|[^:\[\]]+):(\d+)$")


def local_host_header(host, port):
    """Whether the request was addressed to this collector, not to a name rebound onto it."""
    m = HOST_HEADER.match(host or "")
    return bool(m) and m.group(1).strip("[]") in LOCAL_HOSTS and int(m.group(2)) == port


def make_server(port, deliver, origins):
    """The collector: GET /rails-inspector.js serves the reporter, POST /report takes what it sends."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        @property
        def port(self):
            return self.server.server_address[1]

        def allowed(self):
            if not local_host_header(self.headers.get("Host"), self.port):
                return False
            return local_origin(self.headers.get("Origin"), origins())

        def deny(self):
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def cors(self):
            origin = self.headers.get("Origin")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def do_OPTIONS(self):
            if not self.allowed():
                return self.deny()
            self.send_response(204)
            self.cors()
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path != "/rails-inspector.js":
                return self.send_error(404)
            if not local_host_header(self.headers.get("Host"), self.port):
                return self.deny()
            project = (urllib.parse.parse_qs(url.query).get("project") or [""])[0][:200]
            body = (SNIPPET % {"project": json.dumps(project),
                               "url": json.dumps("http://127.0.0.1:%d/report" % self.port)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != "/report":
                return self.send_error(404)
            if not self.allowed():
                return self.deny()
            length = to_int(self.headers.get("Content-Length"))
            if length <= 0 or length > MAX_BODY:
                return self.deny()
            try:
                report = json.loads(self.rfile.read(length))
            except ValueError:
                report = None
            if isinstance(report, dict):
                deliver(report)
            self.send_response(204)
            self.cors()
            self.end_headers()

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    return Server(("127.0.0.1", port), Handler)
