import json
import unittest

import helper
from rails_inspector.sourcemaps import SourceMaps

BUNDLE = "http://localhost:3000/assets/application.debug.js"


def write_map(root, mappings, sources=("../../javascript/application.js",)):
    helper.write(root, "app/assets/builds/application.js.map",
                 json.dumps({"version": 3, "sources": list(sources), "mappings": mappings}))


class SourceMapsTest(unittest.TestCase):
    def setUp(self):
        self.root = helper.project_dir()
        # generated line 1 → nothing; line 2 col 0 → source 0 line 10; line 3 col 4 → source 0 line 12
        write_map(self.root, ";" + helper.vlq(0, 0, 9, 0) + ";" + helper.vlq(4, 0, 2, 0))
        self.maps = SourceMaps()

    def test_resolve(self):
        self.assertEqual(self.maps.resolve(self.root, BUNDLE, 2, 1), "app/javascript/application.js:10")
        self.assertEqual(self.maps.resolve(self.root, BUNDLE, 3, 9), "app/javascript/application.js:12")
        self.assertIsNone(self.maps.resolve(self.root, BUNDLE, 1, 1))
        self.assertIsNone(self.maps.resolve(self.root, BUNDLE, 99, 1))

    def test_digest_and_plain_names(self):
        self.assertIsNotNone(self.maps.resolve(self.root, "http://x/assets/application-0123abcd.js", 2, 1))
        self.assertIsNotNone(self.maps.resolve(self.root, "/assets/application.js?v=1", 2, 1))

    def test_a_map_outside_the_build_folders_is_not_read(self):
        helper.write(self.root, "secret.js.map", json.dumps({"version": 3, "sources": ["x.js"], "mappings": ";AAAA"}))
        self.assertIsNone(self.maps.map_file(self.root, "/assets/../../secret.js"))
        self.assertIsNone(self.maps.resolve(self.root, "http://x/assets/../../secret.js", 2, 1))

    def test_locate_prefers_own_code(self):
        write_map(self.root, ";" + helper.vlq(0, 0, 0, 0) + ";" + helper.vlq(0, 1, 4, 0),
                  sources=("../../../node_modules/lib/index.js", "../../javascript/own.js"))
        stack = "at a (%s:2:1)\n at b (%s:3:1)" % (BUNDLE, BUNDLE)
        self.assertEqual(SourceMaps().locate(self.root, "", 0, 0, stack), "app/javascript/own.js:5")

    def test_translate_stack(self):
        out = self.maps.translate_stack(self.root, "at x (%s:2:1)" % BUNDLE)
        self.assertEqual(out, "at x (app/javascript/application.js:10)")

    def test_missing_or_broken_map(self):
        self.assertIsNone(self.maps.resolve(self.root, "http://x/assets/admin.js", 2, 1))
        write_map(self.root, "!!!;" + helper.vlq(0, 0, 0, 0))
        self.assertIsNone(SourceMaps().resolve(self.root, BUNDLE, 1, 1))
        helper.write(self.root, "app/assets/builds/application.js.map", "{not json")
        self.assertIsNone(SourceMaps().resolve(self.root, BUNDLE, 2, 1))

    def test_unmapped_location_falls_back_to_the_url_path(self):
        self.assertEqual(self.maps.locate(self.root, "http://localhost:3000/", 25, 1, ""), "/:25")


if __name__ == "__main__":
    unittest.main()
