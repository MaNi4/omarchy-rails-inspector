.pragma library

// What Panel.qml and Service.qml show and decide, without any QML in it,
// so tests/run.js can check it with plain node.

var KINDS = [
  { key: "error", label: "Errors" },
  { key: "n1", label: "N+1" },
  { key: "slow_request", label: "Slow requests" },
  { key: "slow_query", label: "Slow queries" },
  { key: "deprecation", label: "Deprecations" },
  { key: "warning", label: "Warnings" },
  { key: "js", label: "JavaScript" }
]
var ALARMING = ["error", "js"]
var KEPT_DETAILS = 50
var THRESHOLD_SETTINGS = [
  { key: "slowRequestMs", label: "Slow request (ms)", from: 1, to: 600000 },
  { key: "slowQueryMs", label: "Slow query (ms)", from: 1, to: 600000 },
  { key: "nPlusOne", label: "N+1: same query from one line (×)", from: 2, to: 1000 },
  { key: "manyQueries", label: "Too many queries in one request", from: 2, to: 100000 }
]
var PORT_SETTING = { key: "jsPort", label: "Collector port", from: 1024, to: 65535 }

function freshIn(project, kind) { return project && project.fresh ? Number(project.fresh[kind] || 0) : 0 }
function countIn(project, kind) { return project && project.counts ? Number(project.counts[kind] || 0) : 0 }

function freshAlarms(project) {
  var n = 0
  for (var i = 0; i < ALARMING.length; i++) n += freshIn(project, ALARMING[i])
  return n
}

function freshAll(project) {
  var n = 0
  for (var i = 0; i < KINDS.length; i++) n += freshIn(project, KINDS[i].key)
  return n
}

function alarmTotal(projects) {
  var n = 0
  for (var i = 0; i < (projects || []).length; i++) n += freshAlarms(projects[i])
  return n
}

// The project asked for by name, or the first one when it is gone.
function chooseProject(projects, name) {
  projects = projects || []
  for (var i = 0; i < projects.length; i++) if (projects[i].name === name) return projects[i]
  return projects[0] || null
}

function projectOptions(projects) {
  return (projects || []).map(function(p) {
    var alarms = freshAlarms(p)
    var fresh = freshAll(p)
    return {
      value: p.name,
      label: p.name + (alarms > 0 ? "  ·  " + alarms + " new errors" : fresh > 0 ? "  ·  " + fresh + " new" : ""),
      description: p.error || p.notice || p.path
    }
  })
}

function rowsOf(project, kind) {
  if (!project) return []
  return (project.problems || []).filter(function(p) { return p.kind === kind })
}

// The first tab with something new, to open the panel on; null when nothing is new.
function firstFreshTab(project) {
  for (var i = 0; i < KINDS.length; i++) if (freshIn(project, KINDS[i].key) > 0) return KINDS[i].key
  return null
}

function nextTab(tab, step) {
  var i = 0
  for (; i < KINDS.length; i++) if (KINDS[i].key === tab) break
  if (i === KINDS.length) i = 0
  return KINDS[((i + step) % KINDS.length + KINDS.length) % KINDS.length].key
}

// Up/down in a list of `count` rows: the first press shows the cursor on the first row.
function moveCursor(cursor, step, count) {
  if (!count) return { active: false, index: 0 }
  if (!cursor.active) return { active: true, index: 0 }
  return { active: true, index: Math.max(0, Math.min(count - 1, cursor.index + step)) }
}

function timeAgo(epoch, now) {
  if (!epoch) return ""
  var d = Math.max(0, Math.floor(now - Number(epoch)))
  if (d < 60) return "just now"
  if (d < 3600) return Math.floor(d / 60) + " min ago"
  if (d < 86400) return Math.floor(d / 3600) + " h ago"
  return Math.floor(d / 86400) + " d ago"
}

function isFresh(project, problem) { return !!project && Number(problem.last) > Number(project.seen || 0) }
function isAlarm(problem) { return problem.kind === "error" || (problem.kind === "js" && problem.level !== "warning") }

function subtitle(problem, now) {
  var bits = []
  if (Number(problem.count) > 1) bits.push("×" + problem.count)
  if (problem.where) bits.push(problem.where)
  if (problem.sub) bits.push(problem.sub)
  bits.push(timeAgo(problem.last, now))
  return bits.filter(function(b) { return b !== "" }).join("  ·  ")
}

function history(problem, now) {
  if (Number(problem.count) <= 1) return ""
  var worst = problem.worst ? "  ·  worst " + Math.round(problem.worst) + (problem.kind === "n1" ? " queries" : "ms") : ""
  return "First seen " + timeAgo(problem.first, now) + worst
}

function tabLabel(kind, project) {
  var total = countIn(project, kind.key)
  var fresh = freshIn(project, kind.key)
  return kind.label + (total > 0 ? "  " + total : "") + (fresh > 0 ? "  ●" : "")
}

function tooltip(projects) {
  if (!projects || !projects.length) return "Rails Inspector · add a project"
  var parts = []
  for (var i = 0; i < projects.length; i++) {
    var bits = []
    for (var k = 0; k < KINDS.length; k++) {
      var n = freshIn(projects[i], KINDS[k].key)
      if (n > 0) bits.push(n + " " + KINDS[k].label.toLowerCase())
    }
    if (bits.length) parts.push(projects[i].name + ": " + bits.join(", "))
  }
  return "Rails Inspector · " + (parts.length ? parts.join(" · ") : "nothing new")
}

function settingsLine(thresholds, js) {
  var t = thresholds || {}
  js = js || {}
  var bits = []
  if (t.slowRequestMs) bits.push("slow ≥ " + t.slowRequestMs + "ms")
  if (t.slowQueryMs) bits.push("SQL ≥ " + t.slowQueryMs + "ms")
  if (t.nPlusOne) bits.push("N+1 ≥ " + t.nPlusOne + "×")
  if (js.listening) bits.push("JS: " + js.port)
  else if (js.error) bits.push("JS off")
  return bits.join("  ·  ")
}

function emptyText(kind, setup, project, now) {
  if (kind !== "js") return "Nothing here"
  if (setup && !setup.ready) return ""
  return project && project.connectedAt
    ? "Connected · no browser errors (last page " + timeAgo(project.connectedAt, now) + ")"
    : "Nothing here"
}

// The whole setup guide, until a page of the project has reached the collector.
function guideSteps(setup) {
  return setup && !setup.ready && setup.steps ? setup.steps : []
}

function setupHeading(projectName) {
  return "No page of " + projectName + " has reached the collector yet"
}

var SETUP_HINT = "Once the reporter loads in the app's pages, the first page load makes this guide go away, "
  + "however the app loads it. Marked ✓ is what is already seen in the project."

function originsText(settings) {
  return settings && settings.jsOrigins ? settings.jsOrigins.join(" ") : ""
}

function ignoredText(count) {
  if (!count) return "Nothing is ignored."
  return count + (count === 1 ? " problem is" : " problems are") + " ignored and never shown."
}

function kindLabel(kind) {
  for (var i = 0; i < KINDS.length; i++) if (KINDS[i].key === kind) return KINDS[i].label
  return ""
}

// The ignored problems of every project, one row each; the project is named only when there are several.
function ignoredRows(projects) {
  var rows = []
  projects = projects || []
  for (var i = 0; i < projects.length; i++) {
    var ignored = projects[i].ignored || []
    for (var k = 0; k < ignored.length; k++) {
      var p = ignored[k]
      var sub = [kindLabel(p.kind), p.where, projects.length > 1 ? projects[i].name : ""].filter(function(x) { return !!x })
      rows.push({ project: projects[i].name, fp: p.fp, title: p.title || "A problem ignored earlier", sub: sub.join("  ·  ") })
    }
  }
  return rows
}

// What Copy puts on the clipboard: the problem, a blank line, its detail.
function copyText(problem, detail) {
  var head = [problem.title, problem.sub, problem.where].filter(function(x) { return !!x })
  return detail ? head.concat(["", detail]).join("\n") : head.join("\n")
}

function detailKey(projectName, fp) { return projectName + "/" + fp }

// The details asked for, the newest KEPT_DETAILS of them; a problem that is gone has "".
function keepDetail(details, detail) {
  var key = detailKey(detail.project, detail.fp)
  var keys = Object.keys(details || {}).filter(function(k) { return k !== key })
  var next = {}
  for (var i = Math.max(0, keys.length - (KEPT_DETAILS - 1)); i < keys.length; i++) next[keys[i]] = details[keys[i]]
  next[key] = detail.text === null || detail.text === undefined ? "" : String(detail.text)
  return next
}

// Feed lines are the whole status, one requested detail or one project's browser setup.
function parseLine(line) {
  var d
  try { d = JSON.parse(String(line)) } catch (e) { return null }
  if (!d || typeof d !== "object" || Array.isArray(d)) return null
  if (d.detail && typeof d.detail === "object") return { detail: d.detail }
  if (d.setup && typeof d.setup === "object") return { setup: d.setup }
  if (Array.isArray(d.projects)) return { status: d }
  return null
}

// One stdin line for the feed: words joined by spaces, with no line break inside any of them.
function command(words) {
  return words.map(function(w) { return String(w).replace(/[\r\n]/g, " ") }).join(" ") + "\n"
}
