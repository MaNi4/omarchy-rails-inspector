import hashlib
import time

from .files import local_file

KINDS = ["error", "n1", "slow_request", "slow_query", "deprecation", "warning", "js"]
PER_KIND = 100
SUMMARY = ("fp", "kind", "title", "sub", "where", "count", "first", "last", "worst", "level", "openable")


def fingerprint(*parts):
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]


class ProblemStore:
    """The problems of one project, one entry per fingerprint with a count."""

    def __init__(self, project_path, problems=None, ignored=lambda: (), notify=None):
        self.project_path = project_path
        self.problems = problems if isinstance(problems, dict) else {}
        self.ignored = ignored
        self.notify = notify
        self.dirty = False
        for p in self.problems.values():
            if "openable" not in p:
                p["openable"] = local_file(project_path, p.get("where")) is not None

    def record(self, kind, key, title, sub="", where="", detail="", value=None, notify=False, level=None):
        fp = fingerprint(kind, *key)
        if fp in self.ignored():
            return None
        now = time.time()
        p = self.problems.get(fp)
        if p is None:
            p = {"fp": fp, "kind": kind, "first": now, "count": 0}
            self.problems[fp] = p
            if notify and self.notify:
                self.notify(title, sub or where)
        if p.get("where") != where[:300]:
            p["openable"] = local_file(self.project_path, where) is not None
        p.update({"title": title[:300], "sub": sub[:300], "where": where[:300], "detail": detail[:8000], "last": now})
        p["count"] += 1
        if value is not None:
            p["worst"] = max(float(p.get("worst") or 0), value)
        if level:
            p["level"] = level
        self.trim(kind)
        self.dirty = True
        return p

    def trim(self, kind):
        same = [p for p in self.problems.values() if p["kind"] == kind]
        if len(same) > PER_KIND:
            for p in sorted(same, key=lambda p: p["last"])[:len(same) - PER_KIND]:
                del self.problems[p["fp"]]

    def remove(self, fp):
        if self.problems.pop(fp, None) is not None:
            self.dirty = True

    def clear(self, kind):
        self.problems = {fp: p for fp, p in self.problems.items() if kind != "all" and p["kind"] != kind}
        self.dirty = True

    def detail(self, fp):
        p = self.problems.get(fp)
        return p.get("detail", "") if p else None

    def summary(self, seen):
        problems = sorted(self.problems.values(), key=lambda p: -p["last"])
        counts = {k: 0 for k in KINDS}
        fresh = {k: 0 for k in KINDS}
        for p in problems:
            counts[p["kind"]] = counts.get(p["kind"], 0) + 1
            if p["last"] > seen:
                fresh[p["kind"]] = fresh.get(p["kind"], 0) + 1
        return {"counts": counts, "fresh": fresh,
                "problems": [{k: p[k] for k in SUMMARY if k in p} for p in problems]}
