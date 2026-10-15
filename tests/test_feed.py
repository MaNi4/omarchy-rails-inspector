import io
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import helper
from rails_inspector import config as conf
from rails_inspector import feed


class FeedCase(unittest.TestCase):
    """A feed over one made-up project, with its config and state in a directory of its own."""

    def setUp(self):
        self.root = helper.project_dir()
        self.tmp = tempfile.mkdtemp(prefix="rails-inspector-feed-")
        self.patches = [mock.patch.object(conf, "CONFIG", os.path.join(self.tmp, "cfg.json")),
                        mock.patch.object(conf, "STATE_DIR", os.path.join(self.tmp, "state")),
                        mock.patch.object(feed, "events", queue.Queue())]
        for p in self.patches:
            p.start()
        conf.save_json(conf.CONFIG, {"projects": [{"path": self.root}], "notify": False})
        helper.write(self.root, "log/development.log", "")
        helper.write(self.root, "app/models/user.rb", "x\n")
        self.inspector = feed.Inspector()
        self.project = self.inspector.projects[0]
        self.project.poll()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def append(self, text):
        with open(self.project.log, "a") as f:
            f.write(text)
        while self.project.poll():
            pass
        self.project.parser.idle()

    def output(self, fn, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*args)
        return [json.loads(line) for line in buf.getvalue().splitlines()]

    def error(self):
        self.append('Started GET "/x" for 127.0.0.1 at now\nProcessing by XController#show as HTML\n'
                    'Completed 500 Internal Server Error in 3ms (GC: 0.0ms)\n\n'
                    'NoMethodError (undefined method `y\' for nil):\n\napp/models/user.rb:1:in `y\'\n\n')
        [p] = self.project.store.problems.values()
        return p


class FeedTest(FeedCase):
    def test_watches_from_the_end_and_finds_new_problems(self):
        p = self.error()
        self.assertEqual(p["where"], "app/models/user.rb:1")
        self.assertTrue(p["openable"])

    def test_status_is_lean_and_detail_comes_on_request(self):
        p = self.error()
        [status] = self.output(self.inspector.emit)
        row = status["projects"][0]["problems"][0]
        self.assertNotIn("detail", row)
        [answer] = self.output(self.inspector.command, "detail %s %s" % (self.project.name, p["fp"]))
        self.assertIn("NoMethodError", answer["detail"]["text"])
        [missing] = self.output(self.inspector.command, "detail %s nope" % self.project.name)
        self.assertIsNone(missing["detail"]["text"])

    def test_a_log_in_another_format_is_named_in_the_status(self):
        self.project.poll()
        self.append('{"method":"GET","path":"/","status":200}\n' * 250)
        self.assertIn("JSON", self.project.view()["notice"])
        self.assertTrue(self.project.dirty)

    def test_pages_are_matched_by_the_directory_or_by_the_application_name(self):
        helper.write(self.root, "config/application.rb", "require_relative \"boot\"\n\nmodule ShopV2\n  class Application < Rails::Application\n  end\nend\n")
        self.assertIs(self.inspector.project_for(self.project.name.upper()), self.project)
        self.assertIs(self.inspector.project_for("shop-v2"), self.project)
        self.assertIs(self.inspector.project_for("ShopV2"), self.project)
        self.assertIsNone(self.inspector.project_for("blog"))
        self.assertIsNone(self.inspector.project_for(""))

    def test_setup_is_checked_on_request_against_the_collector_port(self):
        self.inspector.js = {"port": 9791, "listening": True, "error": ""}
        [answer] = self.output(self.inspector.command, "setup %s" % self.project.name)
        self.assertEqual(answer["setup"]["project"], self.project.name)
        self.assertFalse(answer["setup"]["ready"])
        url = next(s for s in answer["setup"]["steps"] if s["key"] == "url")
        self.assertIn("127.0.0.1:9791", url["code"])
        self.assertEqual(self.output(self.inspector.command, "setup nobody"), [])

    def test_a_hello_from_a_page_makes_the_setup_ready_and_is_no_problem(self):
        self.inspector.js = {"port": 9787, "listening": True, "error": ""}
        [answer] = self.output(self.inspector.browser_report, {"project": self.project.name, "type": "hello"})
        self.assertTrue(answer["setup"]["ready"])
        self.assertEqual(self.output(self.inspector.browser_report, {"project": self.project.name, "type": "hello"}), [])
        self.assertEqual(self.project.store.summary(0)["problems"], [])
        self.assertEqual(conf.load_json(self.project.state_path, {})["connectedPort"], 9787)
        self.assertTrue(self.project.dirty)
        self.assertIsNotNone(self.project.view()["connectedAt"])
        self.inspector.js = {"port": 9788, "listening": True, "error": ""}
        [answer] = self.output(self.inspector.command, "setup %s" % self.project.name)
        self.assertFalse(answer["setup"]["ready"])

    def test_open_uses_the_stored_location(self):
        p = self.error()
        with mock.patch.object(feed.subprocess, "Popen") as popen:
            self.inspector.command("open %s %s" % (self.project.name, p["fp"]))
            self.assertEqual(popen.call_args[0][0][-1], os.path.realpath(os.path.join(self.root, "app/models/user.rb")))
            popen.reset_mock()
            self.inspector.command("open %s ../../etc/passwd" % self.project.name)
            popen.assert_not_called()

    def test_ignore_is_kept_in_the_config(self):
        p = self.error()
        self.inspector.command("ignore %s %s" % (self.project.name, p["fp"]))
        self.assertEqual(conf.read_config()["ignore"], {self.project.name: [p["fp"]]})
        self.assertFalse(self.inspector.reload_config())
        self.append('Started GET "/x" for 127.0.0.1 at now\n\n'
                    'NoMethodError (undefined method `y\' for nil):\n\napp/models/user.rb:1:in `y\'\n\n')
        self.assertEqual(self.project.store.problems, {})

    def test_resolve_and_clear(self):
        p = self.error()
        self.inspector.command("resolve %s %s" % (self.project.name, p["fp"]))
        self.assertEqual(self.project.store.problems, {})

    def test_browser_reports_find_their_project_loosely(self):
        name = self.project.name.replace("-", "_").upper()
        self.inspector.browser_report({"project": name, "type": "console", "message": "hi"})
        self.inspector.browser_report({"project": "someone-else", "type": "console", "message": "no"})
        self.assertEqual([p["title"] for p in self.project.store.problems.values()], ["hi"])

    def test_rotated_log_starts_over(self):
        self.error()
        with open(self.project.log, "w") as f:
            f.write("")
        self.project.poll()
        self.assertEqual(self.project.offset, 0)


if __name__ == "__main__":
    unittest.main()


class FeedBehaviourTest(FeedCase):
    def test_a_project_without_a_log_says_so_and_recovers_when_it_appears(self):
        os.remove(self.project.log)
        self.project.poll()
        self.assertEqual(self.project.error, "No log at log/development.log yet")
        helper.write(self.root, "log/development.log", "")
        self.project.poll()
        self.assertEqual(self.project.error, "")

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_a_log_that_cannot_be_read_says_why(self):
        with open(self.project.log, "a") as f:
            f.write("more\n")
        os.chmod(self.project.log, 0)
        self.addCleanup(os.chmod, self.project.log, 0o644)
        self.project.poll()
        self.assertTrue(self.project.error.startswith("Cannot read the log"))

    def test_scan_reads_only_the_end_of_a_long_log(self):
        old = 'Started GET "/old" for 127.0.0.1 at now\nCompleted 200 OK in 9000ms (GC: 0.0ms)\n'
        new = 'Started GET "/new" for 127.0.0.1 at now\nCompleted 200 OK in 9000ms (GC: 0.0ms)\n'
        with open(self.project.log, "w") as f:
            f.write(old + "x" * 3000 + "\n" + new)
        with mock.patch.object(feed, "SCAN_BYTES", 500):
            self.inspector.command("scan %s" % self.project.name)
        [p] = self.project.store.problems.values()
        self.assertIn("/new", p["sub"])
        self.assertIn("Read the last", self.inspector.message["text"])

    def test_a_changed_config_is_taken_up_without_a_restart(self):
        other = helper.project_dir()
        conf.write_config({"slowRequestMs": 5, "projects": [{"path": self.root}, {"path": other}]})
        os.utime(conf.CONFIG, (time.time() + 5, time.time() + 5))
        self.assertTrue(self.inspector.reload_config())
        self.assertEqual(self.inspector.cfg["slowRequestMs"], 5)
        self.assertEqual(len(self.inspector.projects), 2)
        self.assertIs(self.inspector.projects[0], self.project)
        self.assertFalse(self.inspector.reload_config())

    def test_a_new_port_moves_the_collector_and_only_then(self):
        with mock.patch.object(self.inspector, "restart_js") as restart:
            conf.write_config({"notify": True})
            os.utime(conf.CONFIG, (time.time() + 5, time.time() + 5))
            self.inspector.reload_config()
            restart.assert_not_called()
            conf.write_config({"jsPort": 9799})
            os.utime(conf.CONFIG, (time.time() + 10, time.time() + 10))
            self.inspector.reload_config()
            restart.assert_called_once()

    def test_a_taken_port_is_reported_not_fatal(self):
        import socket
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        self.addCleanup(busy.close)
        self.inspector.cfg["jsPort"] = busy.getsockname()[1]
        self.inspector.start_js()
        self.assertFalse(self.inspector.js["listening"])
        self.assertIn("Port %d" % busy.getsockname()[1], self.inspector.js["error"])

    def test_notifications_can_be_turned_off_and_do_not_pile_up(self):
        with mock.patch.object(feed.shutil, "which", return_value="/usr/bin/notify-send"), \
                mock.patch.object(feed.subprocess, "Popen") as popen:
            self.inspector.notify("shop", "first", "")
            popen.assert_not_called()
            self.inspector.cfg["notify"] = True
            self.inspector.notify("shop", "first", "body")
            self.inspector.notify("shop", "second", "body")
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args[0][0][-2:], ["shop · first", "body"])
            self.inspector.last_note -= feed.NOTIFY_EVERY
            self.inspector.notify("shop", "third", "body")
            self.assertEqual(popen.call_count, 2)

    def test_adding_a_project_checks_it_is_rails_and_not_listed_yet(self):
        plain = tempfile.mkdtemp(prefix="not-rails-")
        self.inspector.add_project(plain)
        self.assertIn("Not a Rails application", self.inspector.message["text"])
        self.inspector.add_project(self.root)
        self.assertEqual(self.inspector.message["text"], "Already listed")
        other = helper.project_dir()
        self.inspector.add_project(other)
        self.assertEqual([p.path for p in self.inspector.projects], [self.root, other])
        self.assertEqual(conf.read_config()["projects"][-1], {"path": other})

    def test_a_project_under_home_is_written_with_a_tilde(self):
        home = tempfile.mkdtemp(prefix="home-")
        project = os.path.join(home, "shop")
        os.makedirs(os.path.join(project, "config"))
        open(os.path.join(project, "config", "application.rb"), "w").close()
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.inspector.add_project(project)
        self.assertEqual(conf.read_config()["projects"][-1], {"path": "~/shop"})

    def test_the_chooser_hands_over_a_directory_or_nothing(self):
        with mock.patch.object(feed, "choose", return_value="/somewhere"):
            self.inspector.ask_for_project()
        self.assertEqual(feed.events.get_nowait(), ("added", "/somewhere"))
        with mock.patch.object(feed, "choose", return_value=None):
            self.inspector.ask_for_project()
        self.assertTrue(feed.events.empty())
        with mock.patch.object(feed, "choose", side_effect=RuntimeError("no portal")):
            self.inspector.ask_for_project()
        self.assertIn("No file chooser", self.inspector.message["text"])

    def test_removing_a_project_takes_it_off_the_list_only(self):
        self.inspector.command("project remove %s" % self.project.name)
        self.assertEqual(self.inspector.projects, [])
        self.assertEqual(conf.read_config()["projects"], [])
        self.assertTrue(os.path.isdir(self.root))

    def test_seen_clears_what_is_new(self):
        self.error()
        self.assertEqual(self.project.view()["fresh"]["error"], 1)
        self.inspector.command("seen %s" % self.project.name)
        self.assertEqual(self.project.view()["fresh"]["error"], 0)

    def test_clear_empties_one_tab_or_all(self):
        self.error()
        self.project.store.record("warning", ["w"], "w")
        self.inspector.command("clear %s warning" % self.project.name)
        self.assertEqual([p["kind"] for p in self.project.store.problems.values()], ["error"])
        self.inspector.command("clear %s all" % self.project.name)
        self.assertEqual(self.project.store.problems, {})

    def test_settings_from_the_panel_are_checked_saved_and_applied(self):
        self.assertTrue(self.inspector.command("set notify false"))
        self.inspector.command("set slowQueryMs 250")
        self.inspector.command("set jsOrigins http://shop.test:3000/, https://a.example http://shop.test:3000")
        saved = conf.read_config()
        self.assertEqual((saved["notify"], saved["slowQueryMs"]), (False, 250))
        self.assertEqual(saved["jsOrigins"], ["http://shop.test:3000", "https://a.example"])
        self.assertEqual(self.inspector.cfg["slowQueryMs"], 250)
        [status] = self.output(self.inspector.emit)
        self.assertEqual(status["settings"]["slowQueryMs"], 250)
        self.assertFalse(self.inspector.reload_config())

    def test_a_wrong_setting_is_refused_with_a_reason(self):
        for line in ("set nPlusOne 1", "set jsPort 80", "set slowQueryMs -5", "set slowQueryMs fast",
                     "set notify maybe", "set jsOrigins javascript:alert(1)", "set projects []"):
            self.inspector.command(line)
            self.assertFalse(self.inspector.message["ok"], line)
        self.assertEqual(conf.read_config()["nPlusOne"], conf.DEFAULTS["nPlusOne"])
        self.assertEqual(conf.read_config()["projects"][0]["path"], self.root)

    def test_a_new_port_from_the_panel_moves_the_collector(self):
        with mock.patch.object(self.inspector, "restart_js") as restart:
            self.inspector.command("set jsPort %d" % self.inspector.cfg["jsPort"])
            restart.assert_not_called()
            self.inspector.command("set jsPort 9800")
            restart.assert_called_once()

    def test_an_ignored_problem_is_listed_and_can_show_again_alone(self):
        first = self.error()
        self.inspector.command("ignore %s %s" % (self.project.name, first["fp"]))
        helper.write(self.root, "app/models/order.rb", "x\n")
        self.append("Started GET \"/b\" for 127.0.0.1 at 2026-10-14 10:00:00 +0000\n"
                    "ArgumentError (bad):\n\napp/models/order.rb:1:in `x'\n\n")
        second = next(p for p in self.project.store.problems.values() if p["title"].startswith("ArgumentError"))
        self.inspector.command("ignore %s %s" % (self.project.name, second["fp"]))
        listed = self.project.view()["ignored"]
        self.assertEqual([(i["fp"], i["kind"], i["where"]) for i in listed],
                         sorted([(first["fp"], "error", first["where"]), (second["fp"], "error", second["where"])]))
        self.inspector.command("unignore %s %s" % (self.project.name, first["fp"]))
        self.assertEqual(self.inspector.ignored(self.project.name), {second["fp"]})
        self.assertEqual(list(self.project.ignored_info), [second["fp"]])
        self.assertEqual(conf.load_json(self.project.state_path, {})["ignoredInfo"].keys(), {second["fp"]})

    def test_a_problem_ignored_before_its_title_was_kept_is_still_listed(self):
        conf.write_config({"ignore": {self.project.name: ["abc"]}})
        self.inspector.reload_config()
        self.assertEqual(self.project.view()["ignored"], [{"fp": "abc"}])

    def test_ignored_problems_can_all_show_again(self):
        p = self.error()
        self.inspector.command("ignore %s %s" % (self.project.name, p["fp"]))
        [status] = self.output(self.inspector.emit)
        self.assertEqual(status["ignored"], 1)
        self.inspector.command("unignore")
        self.assertEqual(conf.read_config()["ignore"], {})
        self.assertEqual(self.inspector.ignored(self.project.name), set())

    def test_unknown_commands_and_projects_change_nothing(self):
        self.error()
        before = json.dumps(self.project.store.problems, sort_keys=True)
        for line in ("", "explode", "resolve nobody x", "seen", "frobnicate %s x" % self.project.name):
            self.assertFalse(self.inspector.command(line), line)
        self.assertEqual(json.dumps(self.project.store.problems, sort_keys=True), before)

    def test_what_was_found_survives_a_restart(self):
        p = self.error()
        self.project.seen = 123.0
        self.project.save()
        again = feed.Inspector().projects[0]
        self.assertEqual(again.store.problems[p["fp"]]["count"], 1)
        self.assertEqual((again.offset, again.seen), (self.project.offset, 123.0))

    def test_the_loop_answers_commands_and_saves_on_quit(self):
        feed.events.put(("cmd", "seen %s" % self.project.name))
        feed.events.put(("quit",))
        with mock.patch.object(feed.threading, "Thread"), redirect_stdout(io.StringIO()) as out:
            self.inspector.run()
        statuses = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertGreaterEqual(len(statuses), 1)
        self.assertGreater(conf.load_json(self.project.state_path, {}).get("seen", 0), 0)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(check, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


class FeedRunningTest(FeedCase):
    def test_the_collector_listens_and_moves_to_a_new_port(self):
        import http.client
        first, second = free_port(), free_port()
        self.inspector.cfg["jsPort"] = first
        threading.Thread(target=self.inspector.start_js, daemon=True).start()
        self.assertTrue(wait_for(lambda: self.inspector.js.get("listening") and self.inspector.js["port"] == first))
        self.inspector.cfg["jsPort"] = second
        self.inspector.restart_js()
        self.assertTrue(wait_for(lambda: self.inspector.js["port"] == second and self.inspector.js["listening"]))
        self.addCleanup(lambda: self.inspector.server and (self.inspector.server.shutdown(), self.inspector.server.server_close()))
        c = http.client.HTTPConnection("127.0.0.1", second, timeout=5)
        c.request("GET", "/rails-inspector.js?project=x", headers={"Host": "127.0.0.1:%d" % second})
        self.assertEqual(c.getresponse().status, 200)
        c.close()
        with self.assertRaises(ConnectionRefusedError):
            http.client.HTTPConnection("127.0.0.1", first, timeout=2).request("GET", "/")

    def test_a_report_that_breaks_is_skipped(self):
        with mock.patch.object(feed, "record_report", side_effect=ValueError("bad")), \
                mock.patch.object(feed.sys, "stderr", io.StringIO()) as err:
            self.inspector.browser_report({"project": self.project.name})
        self.assertIn("browser report skipped", err.getvalue())

    def test_the_loop_reads_the_log_takes_reports_and_tells_the_panel(self):
        out = io.StringIO()
        with mock.patch.object(feed, "read_stdin", lambda: None), \
                mock.patch.object(feed.Inspector, "start_js", lambda self: None), \
                mock.patch.object(feed, "write_line", lambda text: out.write(text + "\n")):
            loop = threading.Thread(target=self.inspector.run, daemon=True)
            loop.start()
            feed.put(("js", {"project": self.project.name, "type": "console", "message": "from the page"}))
            with open(self.project.log, "a") as f:
                f.write("NoMethodError (undefined method `y' for nil):\n\napp/models/user.rb:1:in `y'\n\n")
            self.assertTrue(wait_for(lambda: "from the page" in out.getvalue() and "NoMethodError" in out.getvalue()))
            feed.events.put(("quit",))
            loop.join(5)
        self.assertFalse(loop.is_alive())
        last = json.loads(out.getvalue().splitlines()[-1])
        titles = sorted(p["title"] for p in last["projects"][0]["problems"])
        self.assertEqual(titles, ["NoMethodError: undefined method `y' for nil", "from the page"])
        self.assertTrue(os.path.exists(self.project.state_path))
