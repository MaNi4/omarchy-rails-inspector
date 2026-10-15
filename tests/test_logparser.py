import unittest

import helper
from rails_inspector.logparser import LogParser, normalize_sql
from rails_inspector.store import ProblemStore


def parse(text, root=None):
    store = ProblemStore(root or helper.project_dir())
    parser = LogParser(store.record, lambda: helper.SETTINGS)
    parser.feed(text.encode())
    parser.idle()
    return store


def by_kind(store, kind):
    return [p for p in store.problems.values() if p["kind"] == kind]


REQUEST = """Started GET "/users" for 127.0.0.1 at 2026-10-14 10:00:00 +0000
Processing by UsersController#index as HTML
  Parameters: {"q"=>"x"}
  \x1b[1m\x1b[36mUser Load (250.3ms)\x1b[0m  SELECT "users".* FROM "users" WHERE "users"."name" = 'bob' /*app*/
  ↳ app/controllers/users_controller.rb:5:in `index'
  Post Load (0.4ms)  SELECT "posts".* FROM "posts" WHERE "posts"."user_id" = $1  [["user_id", 1]]
  ↳ app/views/users/index.html.slim:3
  Post Load (0.3ms)  SELECT "posts".* FROM "posts" WHERE "posts"."user_id" = $1  [["user_id", 2]]
  ↳ app/views/users/index.html.slim:3
  Post Load (0.3ms)  SELECT "posts".* FROM "posts" WHERE "posts"."user_id" = $1  [["user_id", 3]]
  ↳ app/views/users/index.html.slim:3
Strict loading violation: `User` is marked for strict_loading. The Post association named `:posts` cannot be lazily loaded.
  ↳ app/views/users/index.html.slim:4
Unpermitted parameter: :admin. Context: { controller: UsersController, action: index }
DEPRECATION WARNING: Foo is deprecated. (called from index at app/controllers/users_controller.rb:7)
Completed 200 OK in 1300ms (Views: 20.0ms | ActiveRecord: 251.3ms (4 queries, 0 cached) | GC: 0.0ms)

Started GET "/nope" for 127.0.0.1 at 2026-10-14 10:00:01 +0000

ActionController::RoutingError (No route matches [GET] "/nope"):

Started GET "/boom" for 127.0.0.1 at 2026-10-14 10:00:02 +0000
Processing by BoomController#show as HTML
Completed 500 Internal Server Error in 5ms (ActiveRecord: 0.0ms (0 queries, 0 cached) | GC: 0.0ms)

ActionView::Template::Error (undefined method `name' for nil):
    1: = @x.name
    2:

app/views/boom/show.html.slim:1
app/controllers/boom_controller.rb:3:in `show'

Started GET "/" for 127.0.0.1 at 2026-10-14 10:00:03 +0000
"""


class LogParserTest(unittest.TestCase):
    def setUp(self):
        self.store = parse(REQUEST)

    def test_slow_request(self):
        [p] = by_kind(self.store, "slow_request")
        self.assertEqual(p["title"], "UsersController#index")
        self.assertIn("1300ms", p["sub"])

    def test_n_plus_one_by_source_line(self):
        repeat = [p for p in by_kind(self.store, "n1") if "×" in p["title"]]
        self.assertEqual(len(repeat), 1)
        self.assertEqual(repeat[0]["title"], "Post Load ×3")
        self.assertEqual(repeat[0]["where"], "app/views/users/index.html.slim:3")

    def test_strict_loading(self):
        [p] = [p for p in by_kind(self.store, "n1") if "lazily" in p["title"]]
        self.assertEqual(p["title"], "User#posts lazily loaded")
        self.assertEqual(p["where"], "app/views/users/index.html.slim:4")

    def test_slow_query_with_source(self):
        [p] = by_kind(self.store, "slow_query")
        self.assertEqual(p["where"], "app/controllers/users_controller.rb:5")
        self.assertIn("UsersController#index", p["sub"])

    def test_deprecation_and_warnings(self):
        [d] = by_kind(self.store, "deprecation")
        self.assertEqual(d["where"], "app/controllers/users_controller.rb:7")
        titles = sorted(p["title"] for p in by_kind(self.store, "warning"))
        self.assertEqual(titles, ["RoutingError", "Unpermitted parameters"])

    def test_template_error(self):
        [e] = by_kind(self.store, "error")
        self.assertTrue(e["title"].startswith("ActionView::Template::Error"))
        self.assertEqual(e["where"], "app/views/boom/show.html.slim:1")
        self.assertIn("BoomController#show", e["sub"])

    def test_repeats_count_instead_of_new_entries(self):
        store = ProblemStore(helper.project_dir())
        parser = LogParser(store.record, lambda: helper.SETTINGS)
        parser.feed((REQUEST + REQUEST).encode())
        parser.idle()
        [p] = by_kind(store, "slow_request")
        self.assertEqual(p["count"], 2)

    def test_partial_lines_wait_for_the_rest(self):
        store = ProblemStore(helper.project_dir())
        parser = LogParser(store.record, lambda: helper.SETTINGS)
        half = len(REQUEST) // 2
        parser.feed(REQUEST[:half].encode())
        parser.feed(REQUEST[half:].encode())
        parser.idle()
        self.assertEqual(len(by_kind(store, "slow_request")), 1)


TAGGED = """[a1] Started GET "/a" for 127.0.0.1 at 2026-10-14 10:00:00 +0000
[b2] Started GET "/b" for 127.0.0.1 at 2026-10-14 10:00:00 +0000
[a1] Processing by AController#index as HTML
[b2] Processing by BController#index as HTML
[a1]   Item Load (0.1ms)  SELECT "items".* FROM "items" WHERE "items"."id" = $1  [["id", 1]]
[a1]   ↳ app/models/a.rb:1
[b2]   Item Load (0.1ms)  SELECT "items".* FROM "items" WHERE "items"."id" = $1  [["id", 2]]
[b2]   ↳ app/models/a.rb:1
[a1]   Item Load (0.1ms)  SELECT "items".* FROM "items" WHERE "items"."id" = $1  [["id", 3]]
[a1]   ↳ app/models/a.rb:1
[b2]   Item Load (0.1ms)  SELECT "items".* FROM "items" WHERE "items"."id" = $1  [["id", 4]]
[b2]   ↳ app/models/a.rb:1
[a1] Completed 200 OK in 10ms (Views: 1.0ms | ActiveRecord: 0.2ms (2 queries, 0 cached) | GC: 0.0ms)
[b2] Completed 500 Internal Server Error in 10ms (ActiveRecord: 0.2ms (2 queries, 0 cached) | GC: 0.0ms)
[b2]   
NoMethodError (undefined method `x' for nil):
  
app/models/a.rb:9:in `x'
[a1] Started GET "/c" for 127.0.0.1 at 2026-10-14 10:00:01 +0000
"""


class TaggedLogTest(unittest.TestCase):
    def test_interleaved_requests_are_kept_apart(self):
        store = parse(TAGGED)
        self.assertEqual(by_kind(store, "n1"), [])

    def test_untagged_exception_lines_belong_to_the_tag_before(self):
        store = parse(TAGGED)
        [e] = by_kind(store, "error")
        self.assertEqual(e["where"], "app/models/a.rb:9")
        self.assertIn("BController#index", e["sub"])


class NormalizeTest(unittest.TestCase):
    def test_literals_and_lists(self):
        self.assertEqual(normalize_sql("SELECT * FROM a WHERE id IN (1, 2, 3) AND n = 'x' /*c*/"),
                         "SELECT * FROM a WHERE id IN (?) AND n = ?")



class RaisedProblemsTest(unittest.TestCase):
    """What a Rails 8 application in its default configuration writes, taken from a real log."""

    def test_strict_loading_that_raises_is_an_error_with_its_line(self):
        store = parse("""Started GET "/strict" for 127.0.0.1 at 2026-10-15 11:28:01 +0200
Processing by ProblemsController#strict as */*
  Order Load (0.4ms)  SELECT "orders".* FROM "orders" ORDER BY "orders"."id" ASC LIMIT 1 /*action='strict',application='ShopLocal',controller='problems'*/
  \u21b3 app/controllers/problems_controller.rb:17:in 'ProblemsController#strict'
Completed 500 Internal Server Error in 10ms (ActiveRecord: 0.4ms (1 query, 0 cached) | GC: 0.0ms)


  
ActiveRecord::StrictLoadingViolationError (`Order` is marked for strict_loading. The Customer association named `:customer` cannot be lazily loaded.):
  
app/controllers/problems_controller.rb:17:in 'ProblemsController#strict'
Started GET "/old" for 127.0.0.1 at 2026-10-15 11:28:01 +0200
""")
        [error] = by_kind(store, "error")
        self.assertTrue(error["title"].startswith("ActiveRecord::StrictLoadingViolationError: `Order` is marked"))
        self.assertEqual(error["where"], "app/controllers/problems_controller.rb:17")
        self.assertEqual(by_kind(store, "n1"), [])

    def test_a_deprecation_called_from_inside_a_gem_has_no_location(self):
        store = parse("DEPRECATION WARNING: Order#legacy_total is deprecated (called from "
                      "ActionController::BasicImplicitRender#send_action at /home/u/.gem/ruby/3.4.0/gems/"
                      "actionpack-8.0.5.1/lib/action_controller/metal/basic_implicit_render.rb:8)\n")
        [p] = by_kind(store, "deprecation")
        self.assertEqual(p["where"], "")
        self.assertIn("basic_implicit_render.rb:8", store.detail(p["fp"]))


class LogFormatTest(unittest.TestCase):
    def notice(self, text):
        store = ProblemStore(helper.project_dir())
        parser = LogParser(store.record, lambda: helper.SETTINGS)
        parser.feed(text.encode())
        return parser.notice()

    def test_a_standard_log_has_no_notice(self):
        self.assertEqual(self.notice(REQUEST * 100), "")

    def test_a_json_log_says_so_once_enough_was_read(self):
        line = '{"method":"GET","path":"/","status":200,"duration":12.3}\n'
        self.assertEqual(self.notice(line * 199), "")
        self.assertIn("JSON", self.notice(line * 200))

    def test_a_lograge_log_says_so(self):
        line = "method=GET path=/orders format=html controller=OrdersController action=index status=200 duration=12.3\n"
        self.assertIn("Lograge", self.notice(line * 300))

    def test_anything_else_says_it_is_not_the_standard_log(self):
        self.assertIn("standard Rails development log", self.notice("I, [2026] INFO -- : something\n" * 300))

    def test_a_log_of_jobs_or_the_console_is_recognized_by_its_queries(self):
        line = '  User Load (0.4ms)  SELECT "users".* FROM "users"\n' + "Performing SomeJob from Sidekiq\n" * 300
        self.assertEqual(self.notice(line), "")

    def test_reading_goes_on_after_a_notice(self):
        store = ProblemStore(helper.project_dir())
        parser = LogParser(store.record, lambda: helper.SETTINGS)
        parser.feed(b"{}\n" * 300)
        self.assertNotEqual(parser.notice(), "")
        parser.feed(REQUEST.encode())
        self.assertEqual(parser.notice(), "")

if __name__ == "__main__":
    unittest.main()
