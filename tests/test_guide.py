import unittest

import helper
from rails_inspector.guide import check

LISTENING = {"listening": True, "port": 9787}
SECURE_HEADERS = """rails_inspector = Array(Rails.configuration.x.rails_inspector.url.presence)
SecureHeaders::Configuration.default do |config|
end
"""


class GuideTest(unittest.TestCase):
    def setUp(self):
        self.root = helper.project_dir()

    def set_up_fully(self, port=9787):
        helper.write(self.root, "config/environments/development.rb",
                     "Rails.application.configure do\n  config.x.rails_inspector.url = 'http://127.0.0.1:%d'\nend\n" % port)
        helper.write(self.root, "app/helpers/rails_inspector_helper.rb",
                     "module RailsInspectorHelper\n  def rails_inspector_tag\n  end\nend\n")
        helper.write(self.root, "app/views/layouts/application.html.slim", "html\n  head\n    = rails_inspector_tag\n")

    def steps(self, js=LISTENING, connected_port=None):
        result = check(self.root, js, connected_port)
        return result, {s["key"]: s for s in result["steps"]}

    def test_ready_once_a_page_reached_the_collector_on_its_current_port_however_it_was_set_up(self):
        self.assertTrue(self.steps(connected_port=9787)[0]["ready"])
        self.assertFalse(self.steps(connected_port=9786)[0]["ready"])
        self.assertFalse(self.steps({"listening": False, "port": 9787}, connected_port=9787)[0]["ready"])

    def test_everything_in_place_is_marked_but_not_ready_before_a_page_reached_the_collector(self):
        self.set_up_fully()
        result, steps = self.steps()
        self.assertFalse(result["ready"])
        self.assertTrue(all(s["ok"] for s in steps.values()))
        self.assertEqual(steps["helper"]["file"], "app/helpers/rails_inspector_helper.rb")
        self.assertIn("def rails_inspector_tag", steps["helper"]["code"])

    def test_a_bare_project_gets_each_step_with_the_code_to_add(self):
        helper.write(self.root, "app/views/layouts/application.html.erb", "<html><head></head></html>")
        result, steps = self.steps()
        self.assertFalse(result["ready"])
        self.assertEqual(steps["url"]["code"], "config.x.rails_inspector.url = 'http://127.0.0.1:9787'")
        self.assertIn("def rails_inspector_tag", steps["helper"]["code"])
        self.assertEqual(steps["layouts"]["code"], "<%= rails_inspector_tag %>")
        self.assertEqual(steps["layouts"]["file"], "app/views/layouts/application.html.erb")
        self.assertNotIn("csp", steps)
        self.assertNotIn("sourcemap", steps)

    def test_the_url_must_point_to_the_port_the_collector_is_on(self):
        self.set_up_fully(port=9787)
        result, steps = self.steps({"listening": True, "port": 9790})
        self.assertFalse(result["ready"])
        self.assertIn("http://127.0.0.1:9787", steps["url"]["note"])
        self.assertEqual(steps["url"]["code"], "config.x.rails_inspector.url = 'http://127.0.0.1:9790'")

    def test_a_url_on_localhost_counts_and_one_from_an_expression_is_not_judged(self):
        self.set_up_fully()
        helper.write(self.root, "config/environments/development.rb",
                     'config.x.rails_inspector.url = "http://localhost:9787"\n')
        self.assertTrue(self.steps()[1]["url"]["ok"])
        helper.write(self.root, "config/environments/development.rb",
                     "config.x.rails_inspector.url = ENV.fetch('INSPECTOR', '')\n")
        self.assertTrue(self.steps()[1]["url"]["ok"])

    def test_a_commented_out_url_does_not_count(self):
        self.set_up_fully()
        helper.write(self.root, "config/environments/development.rb",
                     "# config.x.rails_inspector.url = 'http://127.0.0.1:9787'\n")
        self.assertFalse(self.steps()[1]["url"]["ok"])

    def test_every_layout_with_a_head_needs_the_tag_but_mailers_do_not(self):
        self.set_up_fully()
        helper.write(self.root, "app/views/layouts/admin.html.slim", "html\n  head\n    title Admin\n")
        helper.write(self.root, "app/views/layouts/mailer.html.erb", "<html><head></head></html>")
        result, steps = self.steps()
        self.assertFalse(steps["layouts"]["ok"])
        self.assertEqual(steps["layouts"]["file"], "app/views/layouts/admin.html.slim")
        self.assertEqual(steps["layouts"]["code"], "= rails_inspector_tag")

    def test_the_tag_comes_in_slim_or_haml_when_the_layouts_are_and_in_erb_otherwise(self):
        helper.write(self.root, "app/views/layouts/application.html.haml", "%html\n  %head\n")
        self.assertEqual(self.steps()[1]["layouts"]["code"], "= rails_inspector_tag")
        helper.write(self.root, "app/views/layouts/application.html.haml", "")
        helper.write(self.root, "app/views/layouts/application.html.erb", "<html><head></head></html>")
        self.assertEqual(self.steps()[1]["layouts"]["code"], "<%= rails_inspector_tag %>")

    def test_a_head_in_a_partial_counts(self):
        self.set_up_fully()
        helper.write(self.root, "app/views/layouts/application.html.slim", "html\n  = render 'partials/head'\n")
        helper.write(self.root, "app/views/partials/_head.html.slim", "head\n  = rails_inspector_tag\n")
        self.assertTrue(self.steps()[1]["layouts"]["ok"])

    def test_a_policy_must_allow_the_collector(self):
        self.set_up_fully()
        helper.write(self.root, "config/initializers/content_security_policy.rb",
                     "Rails.application.configure do\n  config.content_security_policy do |policy|\n  end\nend\n")
        steps = self.steps()[1]
        self.assertFalse(steps["csp"]["ok"])
        self.assertIn("policy.connect_src", steps["csp"]["code"])
        helper.write(self.root, "config/initializers/content_security_policy.rb", SECURE_HEADERS)
        steps = self.steps()[1]
        self.assertTrue(steps["csp"]["ok"])
        self.assertEqual(steps["csp"]["file"], "config/initializers/content_security_policy.rb")

    def test_a_policy_left_commented_out_is_no_policy(self):
        self.set_up_fully()
        helper.write(self.root, "config/initializers/content_security_policy.rb",
                     "# Rails.application.configure do\n#   config.content_security_policy do |policy|\n")
        self.assertNotIn("csp", self.steps()[1])

    def test_secure_headers_gets_its_own_code(self):
        self.set_up_fully()
        helper.write(self.root, "config/initializers/secure_headers.rb",
                     "SecureHeaders::Configuration.default do |config|\nend\n")
        self.assertIn("connect_src: %w('self') + rails_inspector", self.steps()[1]["csp"]["code"])

    def test_the_secure_headers_gem_without_its_initializer_still_needs_the_policy(self):
        self.set_up_fully()
        helper.write(self.root, "Gemfile.lock", "GEM\n  specs:\n    secure_headers (7.1.0)\n")
        steps = self.steps()[1]
        self.assertFalse(steps["csp"]["ok"])
        self.assertEqual(steps["csp"]["file"], "config/initializers/secure_headers.rb")

    def test_missing_source_maps_are_optional(self):
        self.set_up_fully()
        helper.write(self.root, "app/assets/builds/application.js", "x")
        steps = self.steps()[1]
        self.assertFalse(steps["sourcemap"]["ok"])
        self.assertTrue(steps["sourcemap"]["optional"])
        helper.write(self.root, "app/assets/builds/application.js.map", "{}")
        self.assertTrue(self.steps()[1]["sourcemap"]["ok"])

    def test_a_collector_that_does_not_listen_says_why(self):
        steps = self.steps({"listening": False, "port": 9787, "error": "Port 9787: Address already in use"})[1]
        self.assertFalse(steps["collector"]["ok"])
        self.assertIn("Address already in use", steps["collector"]["note"])


if __name__ == "__main__":
    unittest.main()
