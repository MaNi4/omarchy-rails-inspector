import http.client
import json
import os
import threading
import time
import unittest

import helper
from rails_inspector import config as conf
from rails_inspector import logparser
from rails_inspector.browser import make_server
from rails_inspector.logparser import LogParser
from rails_inspector.sourcemaps import SourceMaps
from rails_inspector.store import ProblemStore


def parse(text, settings=None, raw=False):
    store = ProblemStore(helper.project_dir())
    parser = LogParser(store.record, lambda: settings or helper.SETTINGS)
    parser.feed(text if raw else text.encode())
    parser.idle()
    return store, parser


def kinds(store):
    return sorted(p["kind"] + ":" + p["title"] for p in store.problems.values())


def request(body, ms=10, action="AController#index"):
    return ('Started GET "/a" for 127.0.0.1 at now\nProcessing by %s as HTML\n%s'
            'Completed 200 OK in %dms (Views: 1.0ms | ActiveRecord: 1.0ms (1 query, 0 cached) | GC: 0.0ms)\n') % (action, body, ms)


def query(i, src="app/models/a.rb:1", name="Item Load", cached=False):
    return '  %s%s (0.1ms)  SELECT "items".* FROM "items" WHERE "items"."id" = $1  [["id", %d]]\n  ↳ %s\n' % (
        "CACHE " if cached else "", name, i, src)


class LogEdgesTest(unittest.TestCase):
    def test_a_quiet_request_leaves_nothing(self):
        store, _ = parse(request(query(1) + query(2), ms=999))
        self.assertEqual(store.problems, {})

    def test_cached_and_bookkeeping_queries_are_not_repeats(self):
        body = "".join(query(i, cached=True) for i in range(5))
        body += "".join('  ActiveRecord::SchemaMigration Load (0.1ms)  SELECT "schema_migrations".* FROM x\n' for _ in range(5))
        store, _ = parse(request(body))
        self.assertEqual(store.problems, {})

    def test_the_same_query_from_different_lines_is_not_one_repeat(self):
        store, _ = parse(request(query(1, "app/a.rb:1") + query(2, "app/b.rb:1") + query(3, "app/c.rb:1")))
        self.assertEqual(store.problems, {})

    def test_many_different_queries_in_one_request_are_reported(self):
        body = "".join(query(i, "app/models/q%d.rb:1" % i) for i in range(61))
        store, _ = parse(request(body))
        self.assertEqual(kinds(store), ["n1:61 queries in one request"])

    def test_a_huge_request_is_counted_whole_but_kept_short(self):
        parser = LogParser(lambda *a, **k: None, lambda: helper.SETTINGS)
        parser.feed(('Started GET "/a" for 127.0.0.1 at now\n' + "".join(
            query(i, "app/q%d.rb:1" % i) for i in range(logparser.MAX_QUERIES + 50))).encode())
        req = parser.contexts[""].req
        self.assertEqual(req.query_count, logparser.MAX_QUERIES + 50)
        self.assertEqual(len(req.queries), logparser.MAX_QUERIES)

    def test_new_thresholds_apply_to_the_next_request(self):
        settings = dict(helper.SETTINGS)
        store = ProblemStore(helper.project_dir())
        parser = LogParser(store.record, lambda: settings)
        parser.feed(request("", ms=500).encode())
        settings["slowRequestMs"] = 400
        parser.feed(request("", ms=500).encode())
        parser.idle()
        [p] = store.problems.values()
        self.assertEqual(p["count"], 1)

    def test_an_exception_message_over_several_lines_is_kept_whole(self):
        store, _ = parse('Started GET "/a" for 127.0.0.1 at now\n\n'
                         'ActiveRecord::PendingMigrationError (\n\nMigrations are pending.\n\n  bin/rails db:migrate\n\n):\n\n'
                         'lib/middleware/x.rb:9:in `call\'\n\n')
        [p] = store.problems.values()
        self.assertEqual(p["title"], "ActiveRecord::PendingMigrationError: Migrations are pending.")
        self.assertIn("bin/rails db:migrate", p["detail"])
        self.assertEqual(p["where"], "lib/middleware/x.rb:9")

    def test_an_exception_at_the_end_of_the_log_is_not_lost(self):
        store, _ = parse("NoMethodError (undefined method `x' for nil):\n\napp/models/a.rb:3:in `x'\n")
        self.assertEqual(kinds(store), ["error:NoMethodError: undefined method `x' for nil"])

    def test_not_found_and_friends_are_warnings_not_errors(self):
        for cls in ("ActiveRecord::RecordNotFound", "ActionController::ParameterMissing", "ActionController::UnknownFormat"):
            store, _ = parse("%s (nope 12):\n\napp/controllers/a.rb:1:in `show'\n\n" % cls)
            self.assertEqual([p["kind"] for p in store.problems.values()], ["warning"], cls)

    def test_ids_in_messages_do_not_make_new_entries(self):
        store, _ = parse("ActiveRecord::RecordNotFound (Couldn't find User with 'id'=12):\n\n"
                         "ActiveRecord::RecordNotFound (Couldn't find User with 'id'=13):\n\n")
        [p] = store.problems.values()
        self.assertEqual(p["count"], 2)

    def test_csrf_and_ruby_warnings(self):
        store, _ = parse("Can't verify CSRF token authenticity.\n/app/models/a.rb:4: warning: method redefined\n")
        self.assertEqual(kinds(store), ["warning:CSRF token not verified", "warning:Ruby warning"])

    def test_a_deprecation_without_a_caller_has_no_place(self):
        store, _ = parse("DEPRECATION WARNING: Something old. Use something new.\n")
        [p] = store.problems.values()
        self.assertEqual((p["title"], p["where"]), ("Something old", ""))

    def test_bytes_that_are_not_utf8_do_not_stop_the_reading(self):
        store, _ = parse(b"Started GET \"/\xff\xfe\" for 127.0.0.1 at now\nCompleted 200 OK in 5000ms (GC: 0.0ms)\n", raw=True)
        self.assertEqual([p["kind"] for p in store.problems.values()], ["slow_request"])

    def test_old_streams_are_closed_and_what_they_held_is_still_reported(self):
        lines = ['[r%d] Started GET "/r%d" for 127.0.0.1 at now\n[r%d] Processing by R%dController#index as HTML\n'
                 % (i, i, i, i) for i in range(logparser.MAX_CONTEXTS + 5)]
        store = ProblemStore(helper.project_dir())
        parser = LogParser(store.record, lambda: helper.SETTINGS)
        parser.feed(("[r0] NoMethodError (lost in the crowd):\n" + "".join(lines[1:])).encode())
        self.assertLessEqual(len(parser.contexts), logparser.MAX_CONTEXTS)
        self.assertNotIn("[r0] ", parser.contexts)
        self.assertEqual(kinds(store), ["error:NoMethodError: lost in the crowd"])


class SourceMapEdgesTest(unittest.TestCase):
    BUNDLE = "http://localhost:3000/assets/application.js"

    def map(self, root, mappings, **extra):
        data = {"version": 3, "sources": ["../../javascript/a.js"], "mappings": mappings}
        data.update(extra)
        helper.write(root, "app/assets/builds/application.js.map", json.dumps(data))

    def test_large_jumps_take_several_characters(self):
        root = helper.project_dir()
        self.map(root, helper.vlq(70000, 0, 4999, 123))
        self.assertGreater(len(helper.vlq(70000, 0, 4999, 123)), 4)
        self.assertEqual(SourceMaps().resolve(root, self.BUNDLE, 1, 70001), "app/javascript/a.js:5000")

    def test_negative_steps_go_back(self):
        root = helper.project_dir()
        self.map(root, helper.vlq(0, 0, 50, 0) + "," + helper.vlq(10, 0, -40, 0))
        maps = SourceMaps()
        self.assertEqual(maps.resolve(root, self.BUNDLE, 1, 1), "app/javascript/a.js:51")
        self.assertEqual(maps.resolve(root, self.BUNDLE, 1, 11), "app/javascript/a.js:11")

    def test_a_column_before_the_first_mapping_takes_the_first(self):
        root = helper.project_dir()
        self.map(root, helper.vlq(8, 0, 2, 0))
        self.assertEqual(SourceMaps().resolve(root, self.BUNDLE, 1, 1), "app/javascript/a.js:3")

    def test_source_root_is_honoured(self):
        root = helper.project_dir()
        self.map(root, helper.vlq(0, 0, 0, 0), sources=["a.js"], sourceRoot="../../javascript/")
        self.assertEqual(SourceMaps().resolve(root, self.BUNDLE, 1, 1), "app/javascript/a.js:1")

    def test_a_rebuilt_map_is_read_again(self):
        root = helper.project_dir()
        maps = SourceMaps()
        self.map(root, helper.vlq(0, 0, 0, 0))
        self.assertEqual(maps.resolve(root, self.BUNDLE, 1, 1), "app/javascript/a.js:1")
        self.map(root, helper.vlq(0, 0, 9, 0))
        later = time.time() + 5
        os.utime(os.path.join(root, "app/assets/builds/application.js.map"), (later, later))
        self.assertEqual(maps.resolve(root, self.BUNDLE, 1, 1), "app/javascript/a.js:10")

    def test_only_library_frames_still_say_where(self):
        root = helper.project_dir()
        self.map(root, helper.vlq(0, 0, 0, 0), sources=["../../../node_modules/lib/x.js"])
        self.assertEqual(SourceMaps().locate(root, self.BUNDLE, 1, 1, ""), "node_modules/lib/x.js:1")

    def test_other_assets_are_not_looked_up(self):
        self.assertIsNone(SourceMaps().map_file(helper.project_dir(), "/packs/application.js"))


class CollectorEdgesTest(unittest.TestCase):
    def setUp(self):
        self.reports = []
        self.origins = ["http://dev.example:3000"]
        self.server = make_server(0, self.reports.append, lambda: self.origins)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def send(self, method, path, body=None, origin=None, host=None, length=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or "127.0.0.1:%d" % self.port}
        if origin:
            headers["Origin"] = origin
        if length is not None:
            headers["Content-Length"] = str(length)
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        r.read()
        c.close()
        return r

    def test_a_preflight_from_a_local_page_is_answered_for_that_page_only(self):
        r = self.send("OPTIONS", "/report", origin="http://localhost:3000")
        self.assertEqual(r.status, 204)
        self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "http://localhost:3000")
        self.assertEqual(self.send("OPTIONS", "/report", origin="https://evil.example").status, 403)

    def test_an_origin_listed_in_the_config_may_report(self):
        r = self.send("POST", "/report", json.dumps({"project": "shop"}), origin="http://dev.example:3000")
        self.assertEqual(r.status, 204)
        self.assertEqual(len(self.reports), 1)
        self.origins = []
        self.assertEqual(self.send("POST", "/report", "{}", origin="http://dev.example:3000").status, 403)

    def test_a_body_too_large_is_refused_unread(self):
        r = self.send("POST", "/report", "x" * 70000, origin="http://localhost:3000")
        self.assertEqual(r.status, 403)
        self.assertEqual(self.reports, [])


class ConfigEdgesTest(unittest.TestCase):
    def test_values_of_the_wrong_kind_fall_back_to_the_defaults(self):
        path = os.path.join(helper.project_dir(), "cfg.json")
        conf.save_json(path, {"slowRequestMs": "fast", "nPlusOne": 0, "jsPort": True, "notify": 1,
                              "projects": {"no": "list"}, "ignore": [], "jsOrigins": "x", "unknown": 1, "slowQueryMs": 250})
        cfg = conf.read_config(path)
        self.assertEqual(cfg["slowRequestMs"], 1000)
        self.assertEqual(cfg["nPlusOne"], 3)
        self.assertEqual(cfg["jsPort"], 9787)
        self.assertIs(cfg["notify"], True)
        self.assertEqual((cfg["projects"], cfg["ignore"], cfg["jsOrigins"]), ([], {}, []))
        self.assertEqual(cfg["slowQueryMs"], 250)
        self.assertNotIn("unknown", cfg)

    def test_a_missing_or_broken_config_is_the_defaults(self):
        root = helper.project_dir()
        self.assertEqual(conf.read_config(os.path.join(root, "none.json")), conf.DEFAULTS)
        broken = helper.write(root, "broken.json", "{")
        self.assertEqual(conf.read_config(broken), conf.DEFAULTS)
        self.assertIsNone(conf.config_mtime(os.path.join(root, "none.json")))

    def test_a_project_is_named_after_its_directory_unless_named(self):
        self.assertEqual(conf.entry_name({"path": "~/Projects/shop/"}), "shop")
        self.assertEqual(conf.entry_name({"path": "~/x", "name": "MyShop"}), "MyShop")
        self.assertEqual(conf.entry_name("~/Projects/blog"), "blog")


class StoreEdgesTest(unittest.TestCase):
    def test_problems_saved_before_openable_existed_learn_it(self):
        root = helper.project_dir()
        helper.write(root, "app/models/a.rb", "x")
        store = ProblemStore(root, {"f": {"fp": "f", "kind": "error", "where": "app/models/a.rb:1", "last": 1, "count": 1},
                                    "g": {"fp": "g", "kind": "error", "where": "/etc/passwd", "last": 1, "count": 1}})
        self.assertTrue(store.problems["f"]["openable"])
        self.assertFalse(store.problems["g"]["openable"])

    def test_a_long_title_and_detail_are_cut(self):
        store = ProblemStore(helper.project_dir())
        p = store.record("error", ["k"], "t" * 1000, detail="d" * 20000)
        self.assertEqual((len(p["title"]), len(p["detail"])), (300, 8000))


if __name__ == "__main__":
    unittest.main()
