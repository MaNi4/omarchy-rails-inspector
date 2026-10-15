import json
import os
import re

FRAME = re.compile(r"(https?://[^\s()]+?):(\d+):(\d+)")
B64 = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}


def decode_vlq(segment):
    values, shift, value = [], 0, 0
    for c in segment:
        digit = B64[c]
        value += (digit & 31) << shift
        if digit & 32:
            shift += 5
        else:
            values.append(-(value >> 1) if value & 1 else value >> 1)
            shift, value = 0, 0
    return values


class SourceMaps:
    """Bundled asset positions (/assets/application.js:1234:5) to their sources, via esbuild's .js.map."""

    def __init__(self):
        self.cache = {}

    def map_file(self, project_path, url_path):
        m = re.match(r"^/assets/(.+?)(?:\.debug)?(?:-[0-9a-f]{7,})?\.js$", url_path)
        if not m:
            return None
        for folder in ("app/assets/builds", "public/assets"):
            candidate = os.path.join(project_path, folder, m.group(1) + ".js.map")
            if os.path.exists(candidate):
                return candidate
        return None

    def load(self, map_path):
        try:
            mtime = os.stat(map_path).st_mtime_ns
        except OSError:
            return None
        cached = self.cache.get(map_path)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            with open(map_path) as f:
                data = json.load(f)
            parsed = self.parse(map_path, data)
        except Exception:
            self.cache[map_path] = (mtime, None)
            return None
        self.cache[map_path] = (mtime, parsed)
        return parsed

    def parse(self, map_path, data):
        root = os.path.join(os.path.dirname(map_path), data.get("sourceRoot") or "")
        sources = [os.path.normpath(os.path.join(root, src)) for src in data.get("sources", [])]
        lines, src, oline, ocol = [], 0, 0, 0
        for text in data.get("mappings", "").split(";"):
            segments, gcol = [], 0
            if text:
                for seg in text.split(","):
                    v = decode_vlq(seg)
                    if not v:
                        continue
                    gcol += v[0]
                    if len(v) >= 4:
                        src += v[1]
                        oline += v[2]
                        ocol += v[3]
                        segments.append((gcol, src, oline, ocol))
            lines.append(segments)
        return (sources, lines)

    def resolve(self, project_path, url, line, col):
        try:
            return self._resolve(project_path, url, line, col)
        except Exception:
            return None

    def _resolve(self, project_path, url, line, col):
        path = re.sub(r"^https?://[^/]+", "", url).split("?")[0]
        map_path = self.map_file(project_path, path)
        parsed = self.load(map_path) if map_path else None
        if not parsed or line < 1 or line > len(parsed[1]):
            return None
        best = None
        for seg in parsed[1][line - 1]:
            if seg[0] <= max(0, col - 1):
                best = seg
            else:
                break
        if best is None and parsed[1][line - 1]:
            best = parsed[1][line - 1][0]
        if best is None:
            return None
        rel = os.path.relpath(parsed[0][best[1]], project_path)
        return "%s:%d" % (rel, best[2] + 1)

    def locate(self, project_path, source, line, col, stack):
        frames = [(source, line, col)] if source and line else []
        frames += [(m.group(1), int(m.group(2)), int(m.group(3))) for m in FRAME.finditer(stack)]
        mapped = [w for w in (self.resolve(project_path, *f) for f in frames) if w]
        own = [w for w in mapped if "node_modules" not in w]
        if own:
            return own[0]
        if mapped:
            return mapped[0]
        path = re.sub(r"^https?://[^/]+", "", source)
        return "%s:%d" % (path, line) if source and line else path

    def translate_stack(self, project_path, stack):
        def swap(m):
            return self.resolve(project_path, m.group(1), int(m.group(2)), int(m.group(3))) or m.group(0)
        return FRAME.sub(swap, stack)
