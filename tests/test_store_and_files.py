import os
import unittest

import helper
from rails_inspector import store as store_module
from rails_inspector.files import editor_command, local_file
from rails_inspector.store import ProblemStore


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.root = helper.project_dir()
        helper.write(self.root, "app/models/user.rb", "class User; end\n")

    def test_same_key_counts(self):
        s = ProblemStore(self.root)
        a = s.record("error", ["X", "a"], "X", detail="d1")
        b = s.record("error", ["X", "a"], "X", detail="d2")
        self.assertIs(a, b)
        self.assertEqual(a["count"], 2)
        self.assertEqual(s.detail(a["fp"]), "d2")

    def test_ignored_and_notify_only_once(self):
        notes = []
        s = ProblemStore(self.root, notify=lambda t, b: notes.append(t))
        p = s.record("error", ["X"], "X", notify=True)
        s.record("error", ["X"], "X", notify=True)
        self.assertEqual(notes, ["X"])
        s.ignored = lambda: {p["fp"]}
        s.remove(p["fp"])
        self.assertIsNone(s.record("error", ["X"], "X"))

    def test_openable_only_for_files_in_the_project(self):
        s = ProblemStore(self.root)
        self.assertTrue(s.record("error", ["a"], "a", where="app/models/user.rb:1")["openable"])
        self.assertFalse(s.record("error", ["b"], "b", where="app/models/gone.rb:1")["openable"])
        self.assertFalse(s.record("js", ["c"], "c", where="/etc/passwd:1")["openable"])

    def test_summary_leaves_details_out(self):
        s = ProblemStore(self.root)
        s.record("warning", ["w"], "w", detail="long text")
        summary = s.summary(0)
        self.assertNotIn("detail", summary["problems"][0])
        self.assertEqual(summary["counts"]["warning"], 1)
        self.assertEqual(summary["fresh"]["warning"], 1)

    def test_trim_keeps_newest(self):
        s = ProblemStore(self.root)
        for i in range(store_module.PER_KIND + 5):
            s.record("warning", [i], str(i))
        self.assertEqual(len(s.problems), store_module.PER_KIND)

    def test_clear_kind(self):
        s = ProblemStore(self.root)
        s.record("warning", ["w"], "w")
        s.record("error", ["e"], "e")
        s.clear("warning")
        self.assertEqual([p["kind"] for p in s.problems.values()], ["error"])


class LocalFileTest(unittest.TestCase):
    def setUp(self):
        self.root = helper.project_dir()
        self.file = helper.write(self.root, "app/models/user.rb", "x")

    def test_inside(self):
        self.assertEqual(local_file(self.root, "app/models/user.rb:12"), (os.path.realpath(self.file), "12"))

    def test_container_root_is_stripped(self):
        self.assertEqual(local_file(self.root, "/app/app/models/user.rb:3")[1], "3")

    def test_outside_is_refused(self):
        self.assertIsNone(local_file(self.root, "../../../../etc/passwd:1"))
        self.assertIsNone(local_file(self.root, "/etc/passwd"))
        self.assertIsNone(local_file(self.root, "/usr/local/bundle/bin/sidekiq:25"))

    def test_symlink_out_is_refused(self):
        os.symlink("/etc/hostname", os.path.join(self.root, "app", "link.rb"))
        self.assertIsNone(local_file(self.root, "app/link.rb"))

    def test_nothing(self):
        self.assertIsNone(local_file(self.root, ""))
        self.assertIsNone(local_file(self.root, None))


class EditorTest(unittest.TestCase):
    def editor(self, name):
        path = helper.write(helper.project_dir(), "editor", name)
        return editor_command("/p/a.rb", "4", default_editor=path)

    def test_variants(self):
        self.assertEqual(self.editor("nvim"), ["omarchy-launch-editor", "+4", "/p/a.rb"])
        self.assertEqual(self.editor("code"), ["code", "-g", "/p/a.rb:4"])
        self.assertEqual(self.editor("zed"), ["omarchy-launch-editor", "/p/a.rb:4"])
        self.assertEqual(editor_command("/p/a.rb", None, default_editor="/nonexistent"),
                         ["omarchy-launch-editor", "/p/a.rb"])


if __name__ == "__main__":
    unittest.main()
