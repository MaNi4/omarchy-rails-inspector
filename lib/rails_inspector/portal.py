import html
import os
import threading
import time
import urllib.parse

signals_lock = threading.Lock()
signals = {"bus": None}


def session_bus():
    from gi.repository import Gio, GLib
    with signals_lock:
        if signals["bus"] is None:
            signals["bus"] = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
        return signals["bus"]


def choose(start, title, wait=600):
    from gi.repository import Gio, GLib
    bus = session_bus()
    token = "railsinsp%d_%d" % (os.getpid(), int(time.time() * 1000) % 1000000)
    request = "/org/freedesktop/portal/desktop/request/%s/%s" % (
        bus.get_unique_name()[1:].replace(".", "_"), token)
    done = threading.Event()
    picked = []

    def answered(_bus, _sender, _path, _iface, _signal, params):
        code, results = params.unpack()
        if code == 0 and results.get("uris"):
            picked.append(results["uris"][0])
        done.set()

    portal = "org.freedesktop.portal.Desktop"
    sub = bus.signal_subscribe(portal, "org.freedesktop.portal.Request", "Response", request, None,
                               Gio.DBusSignalFlags.NONE, answered)
    options = {"handle_token": GLib.Variant("s", token), "directory": GLib.Variant("b", True),
               "current_folder": GLib.Variant("ay", os.fsencode(start) + b"\0")}
    try:
        bus.call_sync(portal, "/org/freedesktop/portal/desktop", "org.freedesktop.portal.FileChooser",
                      "OpenFile", GLib.Variant("(ssa{sv})", ("", title, options)),
                      None, Gio.DBusCallFlags.NONE, 10000, None)
        if not done.wait(wait):
            bus.call_sync(portal, request, "org.freedesktop.portal.Request", "Close", None, None,
                          Gio.DBusCallFlags.NONE, 2000, None)
    finally:
        bus.signal_unsubscribe(sub)
    if not picked:
        return None
    path = urllib.parse.unquote(urllib.parse.urlparse(picked[0]).path)
    return path if os.path.isdir(path) else None


def notify(summary, body):
    # Over D-Bus rather than notify-send, so the text never shows up in process listings.
    # Only the body is markup; the summary is always plain text.
    from gi.repository import Gio, GLib
    params = GLib.Variant("(susssasa{sv}i)", ("Rails Inspector", 0, "", summary, html.escape(body, False), [],
                                              {"urgency": GLib.Variant("y", 1)}, -1))
    session_bus().call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                       "org.freedesktop.Notifications", "Notify", params, None,
                       Gio.DBusCallFlags.NONE, 2000, None, None, None)
