import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
SETTINGS = {"slowRequestMs": 1000, "slowQueryMs": 100, "nPlusOne": 3, "manyQueries": 60}


def vlq(*values):
    out = ""
    for v in values:
        v = (-v << 1) | 1 if v < 0 else v << 1
        while True:
            digit, v = v & 31, v >> 5
            out += B64[digit | (32 if v else 0)]
            if not v:
                break
    return out


def project_dir():
    root = tempfile.mkdtemp(prefix="rails-inspector-test-")
    for d in ("config", "log", "app/models", "app/javascript", "app/assets/builds"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    open(os.path.join(root, "config", "application.rb"), "w").close()
    return root


def write(root, rel, text=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path
