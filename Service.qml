import QtQuick
import Quickshell
import Quickshell.Io
import "Store.js" as Store
import "Model.js" as Model

Item {
  id: service
  visible: false

  Component.onCompleted: Store.set(service)
  Component.onDestruction: Store.clear(service)

  property var status: ({ ok: true, error: "", config: "", js: {}, message: null, projects: [] })
  property bool started: false
  property var details: ({})
  property var setups: ({})
  readonly property string pluginDir: {
    var s = String(Qt.resolvedUrl("."))
    return s.indexOf("file://") === 0 ? s.substring(7) : s
  }

  function trouble(text) {
    var s = {}
    for (var k in status) s[k] = status[k]
    s.ok = false
    s.error = text
    status = s
    started = true
  }

  Process {
    id: feed
    running: true
    command: [service.pluginDir + "bin/rails-inspector"]
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(line) {
        var parsed = Model.parseLine(line)
        if (!parsed) return
        if (parsed.detail) { service.details = Model.keepDetail(service.details, parsed.detail); return }
        if (parsed.setup) {
          var next = {}
          for (var k in service.setups) next[k] = service.setups[k]
          next[parsed.setup.project] = parsed.setup
          service.setups = next
          return
        }
        service.status = parsed.status
        service.started = true
        again.interval = 2000
      }
    }
    onExited: {
      service.trouble("The feed stopped; starting it again")
      again.start()
    }
  }

  Timer {
    id: again
    interval: 2000
    onTriggered: {
      interval = Math.min(interval * 2, 60000)
      feed.running = true
    }
  }

  function send(words) {
    if (!feed.running) return
    feed.write(Model.command(words))
  }

  function addProject() { send(["project", "add"]) }
  function removeProject(name) { send(["project", "remove", name]) }
  function resolve(name, fp) { send(["resolve", name, fp]) }
  function ignore(name, fp) { send(["ignore", name, fp]) }
  function clear(name, kind) { send(["clear", name, kind]) }
  function markSeen(name) { send(["seen", name]) }
  function scan(name) { send(["scan", name]) }
  function open(name, fp) { send(["open", name, fp]) }
  function requestDetail(name, fp) { send(["detail", name, fp]) }
  function requestSetup(name) { send(["setup", name]) }
  function setSetting(key, value) { send(["set", key, value]) }
  function unignore(name, fp) { send(name ? ["unignore", name, fp] : ["unignore"]) }
}
