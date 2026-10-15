import os
import re

CONTAINER_ROOTS = ("/app/", "/rails/", "/usr/src/app/")
LOCATION = re.compile(r"^(.*?)(?::(\d+))?$")
EDITORS_WITH_GOTO = ("code", "cursor", "codium", "windsurf")
EDITORS_WITH_SUFFIX = ("zed", "zeditor", "hx", "helix", "subl", "sublime_text")
DEFAULT_EDITOR = os.path.expanduser("~/.local/state/omarchy/defaults/editor")


def local_file(project_path, location):
    """A location (path[:line]) as (file inside the project, line), or None when it is not a file there."""
    m = LOCATION.match(str(location or "").strip())
    path, line = m.group(1), m.group(2)
    if not path:
        return None
    for prefix in CONTAINER_ROOTS:
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    root = os.path.realpath(project_path)
    full = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
        return None
    return full, line


def editor_command(full, line, default_editor=DEFAULT_EDITOR):
    editor = ""
    try:
        with open(default_editor) as f:
            editor = os.path.basename(f.read().strip())
    except OSError:
        pass
    target = full + (":" + line if line else "")
    if editor in EDITORS_WITH_GOTO:
        return [editor, "-g", target]
    if editor in EDITORS_WITH_SUFFIX:
        return ["omarchy-launch-editor", target]
    return ["omarchy-launch-editor"] + (["+" + line] if line else []) + [full]
