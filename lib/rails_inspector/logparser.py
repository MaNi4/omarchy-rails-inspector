import re
from collections import OrderedDict

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOGRAGE = re.compile(r"\bmethod=[A-Z]+ path=\S+.*\bstatus=\d{3}")
UNRECOGNIZED_AFTER = 200
TAGS = re.compile(r"^((?:\[[^\]\n]*\] )+)(.*)$")
STARTED = re.compile(r'^Started (\w+) "(.*?)" for \S+ at (.+)$')
PROCESSING = re.compile(r"^Processing by (\S+) as (\S+)")
PARAMETERS = re.compile(r"^\s+Parameters: (.*)$")
COMPLETED = re.compile(r"^Completed (\d{3}) (.*?) in (\d+)ms(?: \((.*)\))?")
SQL = re.compile(r"^\s{2,}(CACHE )?(.*?)\s*\((\d+(?:\.\d+)?)ms\)\s+(\S.*)$")
SOURCE = re.compile(r"^\s+↳ (.+?)(?::in .*)?$")
STRICT = re.compile(r"is marked for strict.loading", re.I)
DEPRECATION = re.compile(r"^DEPRECATION WARNING: (.*)$")
CALLED_FROM = re.compile(r"\s*\(called from (.+?) at (.+?:\d+)\)\.?\s*$")
EXCEPTION = re.compile(r"^((?:[A-Z]\w*::)+[A-Z]\w*|[A-Z]\w*(?:Error|Exception|Invalid|NotFound|Missing)) \((.*)$")
BACKTRACE = re.compile(r"^\S+:\d+(?::in .*)?$")
SNIPPET_LINE = re.compile(r"^\s+\d+:(?: |$)|^Causes:|^\s*$")
WARNINGS = [
    (re.compile(r"^Unpermitted parameters?: (.*)$"), "Unpermitted parameters"),
    (re.compile(r"^Can't verify CSRF token authenticity"), "CSRF token not verified"),
    (re.compile(r"^(.*?:\d+): warning: (.*)$"), "Ruby warning"),
]
NOT_ERRORS = {"ActionController::RoutingError", "ActiveRecord::RecordNotFound",
              "ActionController::UnknownFormat", "ActionController::InvalidAuthenticityToken",
              "ActionController::ParameterMissing", "AbstractController::ActionNotFound",
              "ActionController::BadRequest"}
QUIET_SQL = ("SchemaMigration", "InternalMetadata", "TRANSACTION", "SAVEPOINT")
MAX_QUERIES = 2000
MAX_CONTEXTS = 256


def normalize_sql(sql):
    s = re.sub(r"/\*.*?\*/", "", sql)
    s = re.sub(r"'(?:[^']|'')*'", "?", s)
    s = re.sub(r"\$\d+|\b\d+(?:\.\d+)?\b", "?", s)
    s = re.sub(r"\(\s*\?(?:\s*,\s*\?)+\s*\)", "(?)", s)
    s = re.sub(r"\[\[.*\]\]\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()[:2000]


def clean_sql(sql):
    return re.sub(r"\s*/\*.*?\*/", "", sql).strip()[:2000]


def app_frame(lines):
    for line in lines:
        if line.startswith(("app/", "lib/", "config/")):
            return re.sub(r":in .*$", "", line)
    return re.sub(r":in .*$", "", lines[0]) if lines else ""


def numbers_out(text):
    return re.sub(r"\d+", "#", text)


class Request:
    def __init__(self, method="", path="", at=""):
        self.method, self.path, self.at = method, path, at
        self.action, self.format, self.params = "", "", ""
        self.queries = []
        self.query_count = 0
        self.status, self.status_text, self.ms, self.breakdown = None, "", None, ""

    def add(self, query):
        if query["name"] and not query["cached"] and not any(s in query["name"] or s in query["sql"] for s in QUIET_SQL):
            self.query_count += 1
            if len(self.queries) < MAX_QUERIES:
                self.queries.append(query)

    def context(self):
        if not (self.action or self.path):
            return ""
        return " ".join(x for x in [self.action, (self.method + " " + self.path).strip()] if x)


class Context:
    """What is open on one stream of the log: a request, a query waiting for its source, an exception."""

    def __init__(self):
        self.req = None
        self.last_req = None
        self.pending = None
        self.exc = None


class LogParser:
    """Turns development.log lines into problems through `record` (ProblemStore.record).

    With config.log_tags = [:request_id] every request (and job) has its own
    context, so interleaved lines of concurrent requests are told apart;
    without tags everything is one stream. Untagged lines continue the
    context of the line before them (exception messages and backtraces).
    """

    def __init__(self, record, settings):
        self.record = record
        self.settings = settings
        self.reset()

    def reset(self):
        self.contexts = OrderedDict()
        self.current = ""
        self.partial = b""
        self.lines = self.recognized = self.json_lines = self.lograge_lines = 0

    def notice(self):
        """Why nothing can be found, once enough of a log that does not look like the standard Rails one was read."""
        if self.recognized or self.lines < UNRECOGNIZED_AFTER:
            return ""
        if self.json_lines * 2 > self.lines:
            return "The log is JSON, not the standard Rails format; nothing can be found in it"
        if self.lograge_lines * 2 > self.lines:
            return "The log is one line per request (Lograge), not the standard Rails format; nothing can be found in it"
        return "Nothing in the log looks like the standard Rails development log; nothing can be found in it"

    # ---------- Input ----------
    def feed(self, data):
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        for raw in lines:
            line = ANSI.sub("", raw.decode("utf-8", "replace")).rstrip("\r")
            if not self.recognized and line.strip():
                self.lines += 1
                self.json_lines += line.lstrip().startswith("{")
                self.lograge_lines += bool(LOGRAGE.search(line))
            self.line(line)

    def idle(self):
        for ctx in list(self.contexts.values()):
            if ctx.pending:
                self.flush_pending(ctx)
            if ctx.exc and ctx.exc["done"]:
                self.finish_exception(ctx)

    def context_for(self, line):
        m = TAGS.match(line)
        if m:
            self.current, line = m.group(1), m.group(2)
        ctx = self.contexts.get(self.current)
        if ctx is None:
            ctx = self.contexts[self.current] = Context()
            while len(self.contexts) > MAX_CONTEXTS:
                _, old = self.contexts.popitem(last=False)
                self.close(old)
        else:
            self.contexts.move_to_end(self.current)
        return ctx, line

    def close(self, ctx):
        if ctx.pending:
            self.flush_pending(ctx)
        if ctx.exc:
            self.finish_exception(ctx)
        if ctx.req:
            self.finish_request(ctx.req)

    # ---------- Lines ----------
    def line(self, raw):
        ctx, line = self.context_for(raw)
        m = SOURCE.match(line)
        if m:
            if ctx.pending:
                ctx.pending["src"] = m.group(1)
                self.flush_pending(ctx)
            return
        if ctx.pending:
            self.flush_pending(ctx)
        if ctx.exc and self.exception_line(ctx, line):
            return

        m = STARTED.match(line)
        if m:
            if ctx.req:
                self.finish_request(ctx.req)
            ctx.req = Request(m.group(1), m.group(2), m.group(3))
            self.recognized += 1
            return
        m = PROCESSING.match(line)
        if m and ctx.req:
            ctx.req.action, ctx.req.format = m.group(1), m.group(2)
            return
        m = PARAMETERS.match(line)
        if m and ctx.req:
            ctx.req.params = m.group(1)[:1000]
            return
        m = COMPLETED.match(line)
        if m:
            req = ctx.req or Request()
            req.status, req.status_text, req.ms = int(m.group(1)), m.group(2), int(m.group(3))
            req.breakdown = m.group(4) or ""
            self.recognized += 1
            self.finish_request(req)
            ctx.last_req, ctx.req = req, None
            return
        m = SQL.match(line)
        if m and not line.lstrip().startswith(("Rendered", "Rendering")):
            q = {"cached": bool(m.group(1)), "name": m.group(2).strip(), "ms": float(m.group(3)),
                 "sql": m.group(4), "src": ""}
            if ctx.req:
                ctx.req.add(q)
            self.recognized += 1
            ctx.pending = {"type": "query", "q": q, "src": "", "req": ctx.req}
            return
        if STRICT.search(line):
            ctx.pending = {"type": "strict", "msg": line.strip(), "src": "", "req": ctx.req}
            return
        m = DEPRECATION.match(line)
        if m:
            self.deprecation(m.group(1), ctx.req)
            return
        m = EXCEPTION.match(line)
        if m:
            rest = m.group(2)
            done = rest.endswith("):")
            ctx.exc = {"cls": m.group(1), "msg": [rest[:-2] if done else rest], "bt": [], "done": done,
                       "req": ctx.req or ctx.last_req}
            return
        for pattern, title in WARNINGS:
            if pattern.match(line):
                self.warning(title, line.strip(), ctx.req)
                return

    def exception_line(self, ctx, line):
        exc = ctx.exc
        if not exc["done"]:
            if STARTED.match(line) or BACKTRACE.match(line):
                exc["done"] = True
            elif line.endswith("):"):
                exc["msg"].append(line[:-2])
                exc["done"] = True
                return True
            else:
                if len(exc["msg"]) < 40:
                    exc["msg"].append(line)
                return True
        if line.startswith("Causes:"):
            exc["causes"] = True
            return True
        if exc.get("causes") and EXCEPTION.match(line):
            return True
        if BACKTRACE.match(line):
            if len(exc["bt"]) < 60:
                exc["bt"].append(line.strip())
            return True
        if SNIPPET_LINE.match(line) and not STARTED.match(line):
            return True
        self.finish_exception(ctx)
        return False

    def flush_pending(self, ctx):
        p, ctx.pending = ctx.pending, None
        if p["type"] == "query":
            p["q"]["src"] = p["src"]
            self.slow_query(p["q"], p["req"])
        else:
            self.strict_loading(p["msg"], p["src"], p["req"])

    # ---------- Problems ----------
    def finish_request(self, req):
        cfg = self.settings()
        where = req.context()
        queries = req.queries
        if req.ms is not None and req.ms >= cfg["slowRequestMs"]:
            slowest = sorted(queries, key=lambda q: -q["ms"])[:5]
            detail = "\n".join(filter(None, [
                "%s %s → %s %s in %dms" % (req.method, req.path, req.status, req.status_text, req.ms),
                req.breakdown and "(" + req.breakdown + ")",
                req.params and "Parameters: " + req.params,
                slowest and "Slowest queries:\n" + "\n".join(
                    "  %.1fms  %s%s" % (q["ms"], clean_sql(q["sql"])[:300], "\n    ↳ " + q["src"] if q["src"] else "")
                    for q in slowest)]))
            self.record("slow_request", [req.method, req.action or req.path], req.action or req.path,
                        "%s %s · %dms · %d %s" % (req.method, req.path, req.ms, req.query_count,
                                                  "query" if req.query_count == 1 else "queries"),
                        "", detail, value=req.ms)
        groups = {}
        for q in queries:
            if q["sql"].lstrip().upper().startswith("SELECT"):
                groups.setdefault((normalize_sql(q["sql"]), q["src"]), []).append(q)
        for (norm, src), same in groups.items():
            if len(same) >= cfg["nPlusOne"]:
                self.record("n1", ["repeat", norm, src], "%s ×%d" % (same[0]["name"] or "Query", len(same)), where, src,
                            "Same query %d× in one request (%.1fms total)\n\n%s\n\nExample:\n%s" % (
                                len(same), sum(q["ms"] for q in same), norm, clean_sql(same[0]["sql"])),
                            value=len(same))
        if req.query_count >= cfg["manyQueries"]:
            self.record("n1", ["many", req.method, req.action or req.path], "%d queries in one request" % req.query_count,
                        where, "", "%s %s ran %d queries" % (req.method, req.path, req.query_count), value=req.query_count)

    def slow_query(self, q, req):
        if q["cached"] or q["ms"] < self.settings()["slowQueryMs"]:
            return
        norm = normalize_sql(q["sql"])
        self.record("slow_query", [norm, q["src"]], "%s · %.0fms" % (q["name"] or "SQL", q["ms"]),
                    req.context() if req else "", q["src"], clean_sql(q["sql"]) + "\n\nNormalized:\n" + norm, value=q["ms"])

    def strict_loading(self, msg, src, req):
        m = re.search(r"`?(\w+)`? is marked for strict.loading.*?named `?:?(\w+)`?", msg)
        title = "%s#%s lazily loaded" % (m.group(1), m.group(2)) if m else "Strict loading violation"
        self.record("n1", ["strict", msg, src], title, req.context() if req else "", src, msg)

    def deprecation(self, text, req):
        m = CALLED_FROM.search(text)
        where = m.group(2) if m else ""
        msg = CALLED_FROM.sub("", text) if m else text
        self.record("deprecation", [msg[:300]], msg.split(". ")[0][:200], req.context() if req else "", where, text)

    def warning(self, title, line, req):
        self.record("warning", [title, numbers_out(line)[:300]], title, (req.context() if req else "") or line[:200], "", line)

    def finish_exception(self, ctx):
        exc, ctx.exc = ctx.exc, None
        msg = "\n".join(exc["msg"]).strip()
        where = app_frame(exc["bt"])
        req = exc["req"]
        detail = "\n".join(filter(None, [
            exc["cls"] + ": " + msg,
            req and req.path and "%s %s%s" % (req.method, req.path, " → %s" % req.status if req.status else ""),
            req and req.params and "Parameters: " + req.params,
            exc["bt"] and "\n".join(exc["bt"][:40])]))
        first = next((l for l in msg.splitlines() if l.strip()), "")
        if exc["cls"] in NOT_ERRORS:
            self.record("warning", [exc["cls"], numbers_out(first)[:200]], exc["cls"].split("::")[-1],
                        first[:200], where, detail)
        else:
            self.record("error", [exc["cls"], where or numbers_out(first)[:200]], "%s: %s" % (exc["cls"], first),
                        req.context() if req else "", where, detail, notify=True)
