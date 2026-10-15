import json
import os
import re

CONFIG = os.path.expanduser("~/.config/omarchy/extensions/rails-inspector.json")
STATE_DIR = os.path.expanduser("~/.local/state/mani4.rails-inspector")
DEFAULTS = {
    "projects": [],
    "slowRequestMs": 1000,
    "slowQueryMs": 100,
    "nPlusOne": 3,
    "manyQueries": 60,
    "jsPort": 9787,
    "jsOrigins": [],
    "notify": True,
    "ignore": {},
}
THRESHOLDS = ("slowRequestMs", "slowQueryMs", "nPlusOne", "manyQueries")
RANGES = {"slowRequestMs": (1, 600000), "slowQueryMs": (1, 600000), "nPlusOne": (2, 1000),
          "manyQueries": (2, 100000), "jsPort": (1024, 65535)}
SETTINGS = ("notify", "jsOrigins") + tuple(RANGES)
ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(?::\d{1,5})?$")


def load_json(path, default):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def read_config(path=None):
    data = load_json(path or CONFIG, {})
    cfg = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        value = data.get(key, default)
        if isinstance(default, bool):
            cfg[key] = value if isinstance(value, bool) else default
        elif isinstance(default, int):
            cfg[key] = value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default
        else:
            cfg[key] = value if isinstance(value, type(default)) else default
    return cfg


def parse_setting(key, raw):
    """A setting changed in the panel, as it is stored; ValueError says what is wrong with it."""
    raw = str(raw).strip()
    if key == "notify":
        if raw not in ("true", "false"):
            raise ValueError("Notifications are on or off")
        return raw == "true"
    if key == "jsOrigins":
        origins = [o.rstrip("/") for o in re.split(r"[\s,]+", raw) if o]
        wrong = [o for o in origins if not ORIGIN.match(o)]
        if wrong:
            raise ValueError("Not an origin like http://shop.test:3000: " + wrong[0][:80])
        return sorted(set(origins), key=origins.index)
    if key in RANGES:
        low, high = RANGES[key]
        if not raw.isdigit() or not low <= int(raw) <= high:
            raise ValueError("%s takes a number from %d to %d" % (key, low, high))
        return int(raw)
    raise ValueError("No such setting: " + key[:40])


def write_config(changes, path=None):
    path = path or CONFIG
    data = load_json(path, {})
    data.update(changes)
    save_json(path, data)


def config_mtime(path=None):
    try:
        return os.stat(path or CONFIG).st_mtime_ns
    except OSError:
        return None


def entry_path(entry):
    raw = entry.get("path") if isinstance(entry, dict) else entry
    return os.path.abspath(os.path.expanduser(str(raw or "")))


def entry_name(entry):
    if isinstance(entry, dict) and entry.get("name"):
        return str(entry["name"])
    return os.path.basename(entry_path(entry).rstrip("/"))
