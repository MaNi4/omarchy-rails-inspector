import glob
import os
import unittest

import helper
from rails_inspector.logparser import LogParser
from rails_inspector.store import ProblemStore

LOGS = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rails-logs", "rails-*.log")))

EXPECTED = {
    ("error", "NoMethodError: undefined method `missing_method_here'", "app.rb:89"),
    ("error", "ActionView::Template::Error: undefined method `name' for nil", "app.rb:93"),
    ("n1", "Customer Load ×5", "app.rb:78"),
    ("n1", "Order#customer lazily loaded", "app.rb:98"),
    ("slow_query", "Order Load", "app.rb:83"),
    ("slow_request", "OrdersController#slow", ""),
    ("deprecation", "old_thing is deprecated, use new_thing", "app.rb:119"),
    ("warning", "Unpermitted parameters", ""),
    ("warning", "RoutingError", None),
}


def problems(path):
    store = ProblemStore(helper.project_dir())
    parser = LogParser(store.record, lambda: helper.SETTINGS)
    with open(path, "rb") as f:
        parser.feed(f.read() + b"\n")
    parser.idle()
    for ctx in list(parser.contexts.values()):
        parser.close(ctx)
    return parser, list(store.problems.values())


class RailsLogsTest(unittest.TestCase):
    """The development log of the same small app (rails-logs/app.rb) under each Rails version."""

    def test_there_are_logs_of_several_rails_versions(self):
        self.assertGreaterEqual(len({os.path.basename(p).split("-")[1] for p in LOGS}), 5)

    def test_every_version_gives_the_same_problems(self):
        for path in LOGS:
            with self.subTest(os.path.basename(path)):
                parser, found = problems(path)
                self.assertEqual(parser.notice(), "")
                for kind, title, where in EXPECTED:
                    match = [p for p in found if p["kind"] == kind and p["title"].startswith(title)]
                    self.assertEqual(len(match), 1, (kind, title, [p["title"] for p in found if p["kind"] == kind]))
                    if where is not None:
                        self.assertEqual(match[0]["where"], where, (kind, title))
                self.assertEqual(len([p for p in found if p["kind"] == "error"]), 2)

    def test_a_template_error_keeps_its_snippet_and_request(self):
        for path in LOGS:
            with self.subTest(os.path.basename(path)):
                found = problems(path)[1]
                [p] = [p for p in found if p["title"].startswith("ActionView::Template::Error")]
                self.assertIn("OrdersController#template", p["sub"])


if __name__ == "__main__":
    unittest.main()
