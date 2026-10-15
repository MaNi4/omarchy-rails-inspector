"""How to set up a Rails project to send its browser errors, and what of it is seen in the project."""

import os
import re
from urllib.parse import urlsplit

URL = re.compile(r"""^\s*config\.x\.rails_inspector\.url\s*=\s*(.*)$""", re.M)
LITERAL = re.compile(r"""^["']([^"']*)["']""")
HELPER = re.compile(r"^\s*def rails_inspector_tag\b", re.M)
HEAD = re.compile(r"<head[\s>]|^\s*%?head\b", re.M)
TAG = re.compile(r"\brails_inspector_tag\b")
RAILS_CSP = re.compile(r"\bcontent_security_policy\s+do\b")
SECURE_HEADERS = re.compile(r"\bSecureHeaders::Configuration\b")
SECURE_HEADERS_GEM = re.compile(r"^    secure_headers \(", re.M)
LOCAL_HOSTS = ("127.0.0.1", "localhost")
MAX_READ = 512 * 1024

HELPER_CODE = """module RailsInspectorHelper
  def rails_inspector_tag
    url = Rails.configuration.x.rails_inspector.url.presence
    return unless url

    project = Rails.application.class.module_parent_name.underscore.dasherize
    tag.script(src: "#{url}/rails-inspector.js?project=#{ERB::Util.url_encode(project)}")
  end
end"""

TAG_CODE = {
    ".slim": "= rails_inspector_tag",
    ".haml": "= rails_inspector_tag",
}
ERB_TAG = "<%= rails_inspector_tag %>"

RAILS_CSP_CODE = """rails_inspector = Array(Rails.configuration.x.rails_inspector.url.presence)

policy.script_src :self, *rails_inspector
policy.connect_src :self, *rails_inspector"""

SECURE_HEADERS_CODE = """rails_inspector = Array(Rails.configuration.x.rails_inspector.url.presence)

script_src: %w('self') + rails_inspector,
connect_src: %w('self') + rails_inspector,"""


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(MAX_READ)
    except OSError:
        return ""


def code_lines(text):
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def files_in(root, sub, suffixes=None):
    found = []
    for base, dirs, names in os.walk(os.path.join(root, sub)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(names):
            if suffixes is None or name.endswith(suffixes):
                found.append(os.path.join(base, name))
    return found


def step(key, title, ok, note="", file="", code="", optional=False):
    return {"key": key, "title": title, "ok": ok, "optional": optional, "note": note, "file": file, "code": code}


def collector_step(js):
    if js.get("listening"):
        return step("collector", "Collector listens on 127.0.0.1:%s" % js.get("port"), True)
    return step("collector", "Collector is not listening", False,
                note=(js.get("error") or "Starting…") + ". Change the collector port in Settings if it is taken.")


def url_step(root, port):
    wanted = "http://127.0.0.1:%s" % port
    file = "config/environments/development.rb"
    code = "config.x.rails_inspector.url = '%s'" % wanted
    note = "At the end of the configure block."
    m = URL.search(code_lines(read(os.path.join(root, file))))
    literal = LITERAL.match(m.group(1).strip()) if m else None
    if m and not literal:
        ok, note = True, "Set from an expression; not checked."
    elif literal:
        try:
            url = urlsplit(literal.group(1))
            ok = url.hostname in LOCAL_HOSTS and url.port == int(port)
        except (TypeError, ValueError):
            ok = False
        if not ok:
            note = "It points to %s; the collector is on %s. Restart the app after the change." % (
                literal.group(1) or "nothing", wanted)
    else:
        ok = False
    return step("url", "Collector URL in development", ok, note=note, file=file, code=code)


def helper_step(root):
    found = next((p for p in files_in(root, "app/helpers", (".rb",)) if HELPER.search(read(p))), None)
    file = os.path.relpath(found, root) if found else "app/helpers/rails_inspector_helper.rb"
    return step("helper", "Helper rails_inspector_tag", bool(found), file=file, code=HELPER_CODE)


def layout_step(root):
    layouts = [p for p in files_in(root, "app/views/layouts")
               if not os.path.basename(p).startswith("mailer") and HEAD.search(read(p))]
    missing = [p for p in layouts if not TAG.search(read(p))]
    anywhere = any(TAG.search(read(p)) for p in files_in(root, "app/views"))
    code = next((TAG_CODE[e] for e in TAG_CODE if any(p.endswith(e) for p in layouts)), ERB_TAG)
    files = ", ".join(os.path.relpath(p, root) for p in missing) or "app/views/layouts/"
    return step("layouts", "Reporter loaded in the layouts", anywhere and not missing, file=files, code=code,
                note="In <head>, before the application's scripts.")


def csp_step(root):
    title = "Content Security Policy allows the collector"
    note = "Add the collector to the script and connect sources you have. Restart the app after the change."
    for path in files_in(root, "config/initializers", (".rb",)):
        text = code_lines(read(path))
        if RAILS_CSP.search(text):
            code = RAILS_CSP_CODE
        elif SECURE_HEADERS.search(text):
            code = SECURE_HEADERS_CODE
        else:
            continue
        return step("csp", title, "rails_inspector" in text, file=os.path.relpath(path, root), code=code, note=note)
    if SECURE_HEADERS_GEM.search(read(os.path.join(root, "Gemfile.lock"))):
        return step("csp", title, False, file="config/initializers/secure_headers.rb", code=SECURE_HEADERS_CODE,
                    note=note)
    return None


def sourcemap_step(root):
    bundles = files_in(root, "app/assets/builds", (".js",))
    if not bundles:
        return None
    return step("sourcemap", "Source maps, to open browser errors in the editor",
                any(os.path.isfile(p + ".map") for p in bundles), file="package.json", optional=True,
                code="--sourcemap", note="Add --sourcemap to the esbuild build (sourcemap: true in a build script); "
                                         "without it the locations point into the bundle.")


def check(root, js, connected_port):
    """Ready once a page of the project has reached the collector on its current port, however it was set up;
    until then each step with what is seen of it in the project."""
    steps = [collector_step(js), url_step(root, js.get("port")), helper_step(root), layout_step(root),
             csp_step(root), sourcemap_step(root)]
    ready = bool(js.get("listening")) and connected_port is not None and connected_port == js.get("port")
    return {"ready": ready, "steps": [s for s in steps if s]}
