"""Feed for the mani4.rails-inspector bar widget.

Watches log/development.log of the Rails projects listed in
~/.config/omarchy/extensions/rails-inspector.json and keeps only what is
wrong: exceptions, N+1 queries and strict loading violations, slow requests,
slow queries, deprecations and warnings. The same problem from the same place
is one entry with a count, not a line per occurrence.

Browser problems come in over HTTP on 127.0.0.1:<jsPort>: in development the
page loads /rails-inspector.js?project=<name> from here, which reports them.

Prints one line of JSON per change on stdout (the status, or a requested
detail); reads commands on stdin:

  project add | project remove NAME
  resolve NAME FP | ignore NAME FP | clear NAME KIND|all
  seen NAME | scan NAME | open NAME FP | detail NAME FP | setup NAME
  set KEY VALUE | unignore [NAME FP]
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

from . import config as conf
from .browser import make_server, record_report
from .files import editor_command, local_file
from .logparser import LogParser
from .portal import choose
from .guide import check
from .sourcemaps import SourceMaps
from .store import ProblemStore

SCAN_BYTES = 5 * 1024 * 1024
CHUNK = 4 * 1024 * 1024
MAX_EVENTS = 2000
NOTIFY_EVERY = 5

out_lock = threading.Lock()
events = queue.Queue()


def put(event):
    if events.qsize() < MAX_EVENTS:
        events.put(event)


def write_line(text):
    with out_lock:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


APP_MODULE = re.compile(r"^\s*module\s+([A-Z]\w*)", re.M)


def match_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def app_name(path):
    """The application's module in config/application.rb, which is the name its pages report."""
    try:
        with open(os.path.join(path, "config", "application.rb"), encoding="utf-8", errors="replace") as f:
            m = APP_MODULE.search(f.read(65536))
    except OSError:
        return ""
    return m.group(1) if m else ""


class Project:
    def __init__(self, inspector, entry):
        self.inspector = inspector
        self.path = conf.entry_path(entry)
        self.name = conf.entry_name(entry)
        self.log = os.path.join(self.path, str(entry.get("log") or "log/development.log"))
        self.state_path = os.path.join(conf.STATE_DIR, re.sub(r"[^\w.-]", "_", self.name) + ".json")
        state = conf.load_json(self.state_path, {})
        self.offset = state.get("offset")
        self.inode = state.get("inode")
        self.seen = float(state.get("seen") or 0)
        self.connected_port = state.get("connectedPort")
        self.connected_at = state.get("connectedAt")
        self.ignored_info = state.get("ignoredInfo") if isinstance(state.get("ignoredInfo"), dict) else {}
        self.error = ""
        self.notice = ""
        self.store = ProblemStore(self.path, state.get("problems"),
                                  ignored=lambda: inspector.ignored(self.name),
                                  notify=lambda title, body: inspector.notify(self.name, title, body))
        self.parser = LogParser(self.store.record, lambda: inspector.cfg)

    @property
    def dirty(self):
        return self.store.dirty

    @dirty.setter
    def dirty(self, value):
        self.store.dirty = value

    def save(self):
        conf.save_json(self.state_path, {"offset": self.offset, "inode": self.inode, "seen": self.seen,
                                         "connectedPort": self.connected_port, "connectedAt": self.connected_at,
                                         "ignoredInfo": self.ignored_info, "problems": self.store.problems})

    def set_error(self, text):
        if self.error != text:
            self.error = text
            self.dirty = True

    def poll(self):
        """Reads what was added to the log; True while there may be more to read."""
        try:
            st = os.stat(self.log)
        except OSError:
            self.set_error("No log at " + os.path.relpath(self.log, self.path) + " yet")
            return False
        self.set_error("")
        if self.offset is None:
            self.offset, self.inode = st.st_size, st.st_ino
            self.dirty = True
            return False
        if st.st_ino != self.inode or st.st_size < self.offset:
            self.offset, self.inode = 0, st.st_ino
            self.parser.reset()
        if st.st_size == self.offset:
            self.parser.idle()
            return False
        try:
            with open(self.log, "rb") as f:
                f.seek(self.offset)
                data = f.read(CHUNK)
        except OSError as e:
            self.set_error("Cannot read the log: " + (e.strerror or str(e)))
            return False
        self.offset += len(data)
        self.parser.feed(data)
        notice = self.parser.notice()
        if notice != self.notice:
            self.notice = notice
            self.dirty = True
        return True

    def scan(self):
        try:
            st = os.stat(self.log)
        except OSError:
            return
        self.parser.reset()
        self.offset = max(0, st.st_size - SCAN_BYTES)
        self.inode = st.st_ino
        if self.offset:
            with open(self.log, "rb") as f:
                f.seek(self.offset)
                self.offset += len(f.readline())
        while self.poll():
            pass
        self.parser.idle()
        self.dirty = True

    def view(self):
        out = {"name": self.name, "path": self.path, "log": self.log, "error": self.error, "seen": self.seen,
               "notice": self.notice, "connectedPort": self.connected_port, "connectedAt": self.connected_at,
               "ignored": [dict(self.ignored_info.get(fp) or {}, fp=fp) for fp in self.inspector.ignored_list(self.name)]}
        out.update(self.store.summary(self.seen))
        return out


class Inspector:
    def __init__(self):
        self.cfg = conf.read_config()
        self.cfg_mtime = conf.config_mtime()
        self.maps = SourceMaps()
        self.projects = []
        self.message = None
        self.js = {"port": self.cfg["jsPort"], "listening": False, "error": ""}
        self.server = None
        self.last_note = 0
        self.load_projects()

    # ---------- Config ----------
    def load_projects(self):
        old = {p.name: p for p in self.projects}
        self.projects = []
        for entry in self.cfg["projects"]:
            if isinstance(entry, str):
                entry = {"path": entry}
            if isinstance(entry, dict) and entry.get("path"):
                name = conf.entry_name(entry)
                self.projects.append(old.get(name) or Project(self, entry))

    def reload_config(self):
        mtime = conf.config_mtime()
        if mtime == self.cfg_mtime:
            return False
        self.cfg_mtime = mtime
        old_port = self.cfg["jsPort"]
        self.cfg = conf.read_config()
        self.load_projects()
        if self.cfg["jsPort"] != old_port:
            self.restart_js()
        return True

    def write_config(self, changes):
        conf.write_config(changes)
        self.cfg_mtime = conf.config_mtime()

    def project(self, name):
        return next((p for p in self.projects if p.name == name), None)

    def project_for(self, name):
        key = match_key(name)
        if not key:
            return None
        return (next((p for p in self.projects if match_key(p.name) == key), None)
                or next((p for p in self.projects if match_key(app_name(p.path)) == key), None))

    def ignored(self, name):
        return set(self.ignored_list(name))

    def ignored_list(self, name):
        found = self.cfg["ignore"].get(name)
        return [str(fp) for fp in found] if isinstance(found, list) else []

    # ---------- Out ----------
    def notify(self, project_name, title, body):
        if not self.cfg["notify"] or not shutil.which("notify-send"):
            return
        now = time.time()
        if now - self.last_note < NOTIFY_EVERY:
            return
        self.last_note = now
        subprocess.Popen(["notify-send", "-a", "Rails Inspector", "-u", "normal", "--",
                          (project_name + " · " + title)[:160], body[:200]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def say(self, text, ok=True):
        self.message = {"text": text, "ok": ok, "ts": time.time()}

    def emit(self):
        write_line(conf_json({"ok": True, "config": conf.CONFIG, "js": self.js, "message": self.message,
                              "thresholds": {k: self.cfg[k] for k in conf.THRESHOLDS},
                              "settings": {k: self.cfg[k] for k in conf.SETTINGS},
                              "ignored": sum(len(self.ignored_list(p.name)) for p in self.projects),
                              "projects": [p.view() for p in self.projects]}))

    # ---------- Commands ----------
    def command(self, line):
        parts = line.strip().split(" ", 2)
        cmd = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""
        if cmd == "project" and arg == "add":
            threading.Thread(target=self.ask_for_project, daemon=True).start()
            return False
        if cmd == "project" and arg == "remove":
            return self.remove_project(rest)
        if cmd == "set":
            return self.set_setting(arg, rest)
        if cmd == "unignore":
            return self.unignore(arg, rest)
        p = self.project(arg)
        if not p:
            return False
        if cmd == "detail":
            write_line(conf_json({"detail": {"project": p.name, "fp": rest, "text": p.store.detail(rest)}}))
            return False
        if cmd == "open":
            self.open(p, rest)
            return False
        if cmd == "setup":
            self.send_setup(p)
            return False
        if cmd == "resolve":
            p.store.remove(rest)
        elif cmd == "ignore":
            ignore = dict(self.cfg["ignore"])
            ignore[p.name] = sorted(set(ignore.get(p.name) or []) | {rest})
            self.cfg["ignore"] = ignore
            self.write_config({"ignore": ignore})
            problem = p.store.problems.get(rest)
            if problem:
                p.ignored_info[rest] = {k: problem.get(k) for k in ("kind", "title", "where")}
            p.store.remove(rest)
            self.say("Ignored; Settings can show it again")
        elif cmd == "clear":
            p.store.clear(rest)
        elif cmd == "seen":
            p.seen = time.time()
        elif cmd == "scan":
            p.scan()
            self.say("Read the last %d MB of %s" % (SCAN_BYTES // 1048576, os.path.basename(p.log)))
        else:
            return False
        p.save()
        return True

    def unignore(self, name, fp):
        """One ignored problem of a project shows again when it happens, or with no name every one of them."""
        ignore = dict(self.cfg["ignore"])
        if name:
            ignore[name] = [f for f in self.ignored_list(name) if f != fp]
            if not ignore[name]:
                ignore.pop(name)
            projects = [p for p in self.projects if p.name == name]
        else:
            ignore = {}
            projects = self.projects
        self.cfg["ignore"] = ignore
        self.write_config({"ignore": ignore})
        for p in projects:
            for gone in [f for f in p.ignored_info if f not in self.ignored(p.name)]:
                del p.ignored_info[gone]
            p.save()
        self.say("Shows again when it happens" if name else "Ignored problems show again when they happen")
        return True

    def set_setting(self, key, raw):
        try:
            value = conf.parse_setting(key, raw)
        except ValueError as e:
            self.say(str(e), False)
            return True
        old = self.cfg[key]
        self.cfg[key] = value
        self.write_config({key: value})
        if key == "jsPort" and value != old:
            self.restart_js()
        return True

    def send_setup(self, project):
        setup = check(project.path, self.js, project.connected_port)
        setup["project"] = project.name
        write_line(conf_json({"setup": setup}))

    def open(self, project, fp):
        problem = project.store.problems.get(fp)
        found = local_file(project.path, problem.get("where")) if problem else None
        if not found:
            self.say("That file is not in the project any more", False)
            put(("emit",))
            return
        subprocess.Popen(editor_command(*found), cwd=project.path, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

    def ask_for_project(self):
        try:
            path = choose(os.path.expanduser("~"), "Rails project to inspect")
        except Exception as e:
            self.say("No file chooser: %s" % e, False)
            put(("emit",))
            return
        if path:
            put(("added", path))

    def add_project(self, path):
        if not os.path.exists(os.path.join(path, "config", "application.rb")):
            self.say("Not a Rails application: " + path, False)
            return
        if any(conf.entry_path(e) == path for e in self.cfg["projects"]):
            self.say("Already listed")
            return
        home = os.path.expanduser("~")
        short = "~" + path[len(home):] if path.startswith(home + os.sep) else path
        self.cfg["projects"] = self.cfg["projects"] + [{"path": short}]
        self.write_config({"projects": self.cfg["projects"]})
        self.load_projects()
        self.say("Added " + os.path.basename(path) + "; watching its log from now")

    def remove_project(self, name):
        self.cfg["projects"] = [e for e in self.cfg["projects"] if conf.entry_name(e) != name]
        self.write_config({"projects": self.cfg["projects"]})
        self.load_projects()
        self.say("Removed " + name)
        return True

    # ---------- Browser ----------
    def start_js(self):
        port = self.cfg["jsPort"]
        try:
            server = make_server(port, lambda report: put(("js", report)), lambda: self.cfg["jsOrigins"])
        except OSError as e:
            self.js = {"port": port, "listening": False, "error": "Port %d: %s" % (port, e.strerror or e)}
            put(("emit",))
            return
        self.server = server
        self.js = {"port": port, "listening": True, "error": ""}
        put(("emit",))
        server.serve_forever()

    def restart_js(self):
        server, self.server = self.server, None

        def swap():
            if server:
                server.shutdown()
                server.server_close()
            self.start_js()

        threading.Thread(target=swap, daemon=True).start()

    def browser_report(self, report):
        p = self.project_for(report.get("project"))
        if not p:
            return
        if report.get("type") == "hello":
            p.connected_at = time.time()
            p.dirty = True
            if p.connected_port != self.js["port"]:
                p.connected_port = self.js["port"]
                p.save()
                self.send_setup(p)
            return
        try:
            record_report(p.store, self.maps, p.path, report)
        except Exception as e:
            sys.stderr.write("rails-inspector: browser report skipped: %r\n" % e)

    # ---------- Loop ----------
    def handle(self, ev):
        kind = ev[0]
        if kind == "cmd":
            return self.command(ev[1])
        if kind == "added":
            self.add_project(ev[1])
            return True
        if kind == "js":
            self.browser_report(ev[1])
            return False
        return kind == "emit"

    def run(self):
        threading.Thread(target=read_stdin, daemon=True).start()
        threading.Thread(target=self.start_js, daemon=True).start()
        self.emit()
        last_emit, last_save = 0.0, time.time()
        while True:
            changed = False
            deadline = time.time() + 1.0
            while time.time() < deadline:
                try:
                    ev = events.get(timeout=max(0.05, deadline - time.time()))
                except queue.Empty:
                    break
                if ev[0] == "quit":
                    for p in self.projects:
                        p.save()
                    return
                changed = self.handle(ev) or changed
            changed = self.reload_config() or changed
            for p in self.projects:
                while p.poll():
                    pass
                changed = changed or p.dirty
            if changed and time.time() - last_emit >= 0.3:
                self.emit()
                last_emit = time.time()
                for p in self.projects:
                    p.dirty = False
            if time.time() - last_save > 10:
                for p in self.projects:
                    p.save()
                last_save = time.time()


def conf_json(data):
    return json.dumps(data, separators=(",", ":"))


def read_stdin():
    for line in sys.stdin:
        put(("cmd", line.rstrip("\n")))
    events.put(("quit",))


def main():
    Inspector().run()
