// node tests/run.js — checks Model.js (what the panel shows and decides) and
// the reporter the collector serves to pages, in a made-up browser.
const fs = require("fs")
const path = require("path")
const vm = require("vm")
const assert = require("assert")

const root = path.join(__dirname, "..")
const source = fs.readFileSync(path.join(root, "Model.js"), "utf8").replace(/^\.pragma library\s*$/m, "")
const Model = new Function(source + `\nreturn { KINDS, ALARMING, KEPT_DETAILS, THRESHOLD_SETTINGS, PORT_SETTING, originsText, ignoredText, ignoredRows, freshIn, countIn, freshAlarms, freshAll,
  alarmTotal, chooseProject, projectOptions, rowsOf, firstFreshTab, nextTab, moveCursor, timeAgo, isFresh, isAlarm,
  subtitle, history, tabLabel, tooltip, settingsLine, emptyText, guideSteps, setupHeading, SETUP_HINT, copyText, detailKey, keepDetail, parseLine,
  command }`)()

const REPORT = "http://127.0.0.1:9787/report"
const REPORTER = "http://127.0.0.1:9787/rails-inspector.js?project=shop"
const BUNDLE = "http://localhost:3000/assets/application.debug.js"
const reporterSource = require("child_process").execFileSync("python3", ["-B", "-c",
  "import json, sys; sys.path.insert(0, sys.argv[1]); from rails_inspector.snippet import SNIPPET; "
  + "print(SNIPPET % {'project': json.dumps('shop'), 'url': json.dumps(sys.argv[2])})",
  path.join(root, "lib"), REPORT], { encoding: "utf8" })

const shop = (fresh, counts, extra) => Object.assign({ name: "shop", path: "/p/shop", seen: 100, fresh: fresh || {}, counts: counts || {},
  problems: [] }, extra)

// ---------- A made-up browser ----------
function listeners() {
  const all = {}
  return {
    addEventListener(type, fn) { (all[type] = all[type] || []).push(fn) },
    dispatch(type, event) { (all[type] || []).forEach(fn => fn(event)) },
    count(type) { return (all[type] || []).length },
  }
}

function browser(options = {}) {
  const sent = []
  const consoleCalls = []
  const win = listeners()
  const doc = listeners()
  const observers = {}
  const fetches = []
  const elements = new Set(options.ids || [])

  class FakeXHR {
    constructor() { this.status = 0; this.handlers = {} }
    addEventListener(type, fn) { this.handlers[type] = fn }
  }
  FakeXHR.prototype.open = function () {}
  FakeXHR.prototype.send = function () { if (options.xhrStatus !== undefined) { this.status = options.xhrStatus; this.handlers.loadend && this.handlers.loadend() } }

  const context = {
    URL, Blob, JSON, Math, Date, Promise, Error, TypeError, Array, String, Object, setTimeout,
    location: { href: "http://localhost:3000/posts?page=2", origin: "http://localhost:3000", pathname: "/posts", search: "?page=2" },
    navigator: {
      sendBeacon(url, blob) {
        if (options.beaconThrows) throw new Error("beacon broke")
        if (options.noBeacon) return false
        sent.push(blob.text().then(text => ({ url, body: JSON.parse(text) })))
        return true
      },
    },
    console: {
      error: (...args) => consoleCalls.push(["error", args]),
      warn: (...args) => consoleCalls.push(["warn", args]),
      assert: (...args) => consoleCalls.push(["assert", args]),
      log: (...args) => consoleCalls.push(["log", args]),
    },
    fetch(url, init) {
      fetches.push({ url: String(url), init })
      if (String(url) === REPORT) {
        sent.push(Promise.resolve({ url: String(url), body: JSON.parse(init.body), viaFetch: true }))
        return Promise.resolve({ status: 204 })
      }
      const answer = (options.fetch || {})[String(url)]
      if (answer instanceof Error) return Promise.reject(answer)
      return Promise.resolve({ status: answer || 200, statusText: answer >= 400 ? "Oops" : "OK" })
    },
    XMLHttpRequest: FakeXHR,
    ReportingObserver: class { constructor(fn, opts) { observers.reporting = { fn, opts } } observe() {} },
    PerformanceObserver: class { constructor(fn) { observers.performance = { fn } } observe(opts) { observers.performance.opts = opts } },
    document: {
      addEventListener: doc.addEventListener,
      getElementById: id => (elements.has(id) ? {} : null),
      querySelector: selector => (elements.has(selector) ? {} : null),
    },
    addEventListener: win.addEventListener,
  }
  context.window = context
  vm.createContext(context)
  new vm.Script(reporterSource, { filename: REPORTER }).runInContext(context)
  const page = (code) => new vm.Script(code, { filename: BUNDLE }).runInContext(context)
  const all = async () => (await Promise.all(sent)).map(s => s.body)
  const reports = async () => (await all()).filter(r => r.type !== "hello")
  const settle = () => new Promise(resolve => setTimeout(resolve, 0))
  return { context, win, doc, observers, page, all, reports, settle, consoleCalls, fetches, sent, reload() {
    new vm.Script(reporterSource, { filename: REPORTER }).runInContext(context)
  } }
}

const model = {
  "fresh and total counts read the project's numbers, and nothing for no project"() {
    const p = shop({ error: 2, js: 1, warning: 3 }, { error: 5 })
    assert.strictEqual(Model.freshIn(p, "error"), 2)
    assert.strictEqual(Model.countIn(p, "error"), 5)
    assert.strictEqual(Model.freshIn(null, "error"), 0)
    assert.strictEqual(Model.countIn({}, "error"), 0)
    assert.strictEqual(Model.freshAlarms(p), 3)
    assert.strictEqual(Model.freshAll(p), 6)
  },
  "only new errors and JavaScript errors light the icon, across projects"() {
    assert.strictEqual(Model.alarmTotal([shop({ warning: 4 }), shop({ js: 1 }), shop({ error: 2 })]), 3)
    assert.strictEqual(Model.alarmTotal([]), 0)
    assert.strictEqual(Model.alarmTotal(null), 0)
  },
  "the project asked for, or the first one when it is gone"() {
    const a = shop(), b = Object.assign(shop(), { name: "blog" })
    assert.strictEqual(Model.chooseProject([a, b], "blog"), b)
    assert.strictEqual(Model.chooseProject([a, b], "removed"), a)
    assert.strictEqual(Model.chooseProject([], "blog"), null)
  },
  "the project list says what is new, errors first, and the path or the trouble under it"() {
    const [a, b, c] = Model.projectOptions([
      shop({ error: 2, warning: 1 }),
      shop({ warning: 3 }, {}, { name: "blog", path: "/p/blog" }),
      shop({}, {}, { name: "api", error: "No log at log/development.log yet" }),
    ])
    assert.strictEqual(a.label, "shop  ·  2 new errors")
    assert.strictEqual(b.label, "blog  ·  3 new")
    assert.strictEqual(b.description, "/p/blog")
    assert.strictEqual(c.label, "api")
    assert.strictEqual(c.description, "No log at log/development.log yet")
  },
  "a tab lists its own kind only"() {
    const p = shop({}, {}, { problems: [{ kind: "error", fp: "a" }, { kind: "js", fp: "b" }, { kind: "error", fp: "c" }] })
    assert.deepStrictEqual(Model.rowsOf(p, "error").map(r => r.fp), ["a", "c"])
    assert.deepStrictEqual(Model.rowsOf(null, "error"), [])
    assert.deepStrictEqual(Model.rowsOf(shop(), "n1"), [])
  },
  "the panel opens on the first tab with something new"() {
    assert.strictEqual(Model.firstFreshTab(shop({ js: 1, n1: 2 })), "n1")
    assert.strictEqual(Model.firstFreshTab(shop()), null)
    assert.strictEqual(Model.firstFreshTab(null), null)
  },
  "left and right go round the tabs both ways"() {
    assert.strictEqual(Model.nextTab("error", 1), "n1")
    assert.strictEqual(Model.nextTab("js", 1), "error")
    assert.strictEqual(Model.nextTab("error", -1), "js")
    assert.strictEqual(Model.nextTab("warning", -2), "slow_query")
    assert.strictEqual(Model.nextTab("nonsense", 1), "n1")
  },
  "the first key press shows the cursor; it then stays within the list"() {
    assert.deepStrictEqual(Model.moveCursor({ active: false, index: 0 }, 1, 3), { active: true, index: 0 })
    assert.deepStrictEqual(Model.moveCursor({ active: true, index: 2 }, 1, 3), { active: true, index: 2 })
    assert.deepStrictEqual(Model.moveCursor({ active: true, index: 0 }, -1, 3), { active: true, index: 0 })
    assert.deepStrictEqual(Model.moveCursor({ active: true, index: 1 }, 1, 3), { active: true, index: 2 })
    assert.deepStrictEqual(Model.moveCursor({ active: true, index: 5 }, 0, 0), { active: false, index: 0 })
  },
  "times are said roughly, and a clock behind does not go negative"() {
    const now = 1000000
    assert.strictEqual(Model.timeAgo(0, now), "")
    assert.strictEqual(Model.timeAgo(now - 59, now), "just now")
    assert.strictEqual(Model.timeAgo(now - 60, now), "1 min ago")
    assert.strictEqual(Model.timeAgo(now - 3599, now), "59 min ago")
    assert.strictEqual(Model.timeAgo(now - 3600, now), "1 h ago")
    assert.strictEqual(Model.timeAgo(now - 86400 * 3, now), "3 d ago")
    assert.strictEqual(Model.timeAgo(now + 500, now), "just now")
  },
  "new means after the project was last looked at; JavaScript warnings are not alarms"() {
    assert.strictEqual(Model.isFresh(shop(), { last: 101 }), true)
    assert.strictEqual(Model.isFresh(shop(), { last: 100 }), false)
    assert.strictEqual(Model.isFresh(null, { last: 101 }), false)
    assert.strictEqual(Model.isAlarm({ kind: "error" }), true)
    assert.strictEqual(Model.isAlarm({ kind: "js", level: "error" }), true)
    assert.strictEqual(Model.isAlarm({ kind: "js", level: "warning" }), false)
    assert.strictEqual(Model.isAlarm({ kind: "warning" }), false)
  },
  "a row says how often, where, in what and when"() {
    const now = 10000
    assert.strictEqual(Model.subtitle({ count: 3, where: "app/a.rb:1", sub: "AController#index", last: now - 120 }, now),
      "×3  ·  app/a.rb:1  ·  AController#index  ·  2 min ago")
    assert.strictEqual(Model.subtitle({ count: 1, last: now }, now), "just now")
    assert.strictEqual(Model.subtitle({ count: 1 }, now), "")
  },
  "an unfolded repeat says when it started and how bad it got"() {
    const now = 10000
    assert.strictEqual(Model.history({ count: 1, first: now }, now), "")
    assert.strictEqual(Model.history({ count: 2, first: now - 7200, worst: 1234.4, kind: "slow_request" }, now),
      "First seen 2 h ago  ·  worst 1234ms")
    assert.strictEqual(Model.history({ count: 5, first: now, worst: 33, kind: "n1" }, now), "First seen just now  ·  worst 33 queries")
    assert.strictEqual(Model.history({ count: 2, first: now }, now), "First seen just now")
  },
  "a tab shows how many and a dot for new ones"() {
    const kind = Model.KINDS[0]
    assert.strictEqual(Model.tabLabel(kind, shop({ error: 1 }, { error: 4 })), "Errors  4  ●")
    assert.strictEqual(Model.tabLabel(kind, shop({}, { error: 4 })), "Errors  4")
    assert.strictEqual(Model.tabLabel(kind, null), "Errors")
  },
  "the icon's tooltip names what is new per project"() {
    assert.strictEqual(Model.tooltip([]), "Rails Inspector · add a project")
    assert.strictEqual(Model.tooltip([shop()]), "Rails Inspector · nothing new")
    assert.strictEqual(Model.tooltip([shop({ error: 1, js: 2 }), Object.assign(shop({ n1: 1 }), { name: "blog" })]),
      "Rails Inspector · shop: 1 errors, 2 javascript · blog: 1 n+1")
  },
  "the header shows the thresholds and whether the collector listens"() {
    const t = { slowRequestMs: 1000, slowQueryMs: 100, nPlusOne: 3 }
    assert.strictEqual(Model.settingsLine(t, { listening: true, port: 9787 }), "slow ≥ 1000ms  ·  SQL ≥ 100ms  ·  N+1 ≥ 3×  ·  JS: 9787")
    assert.strictEqual(Model.settingsLine(t, { listening: false, error: "Port 9787: in use" }), "slow ≥ 1000ms  ·  SQL ≥ 100ms  ·  N+1 ≥ 3×  ·  JS off")
    assert.strictEqual(Model.settingsLine(null, null), "")
  },
  "an empty JavaScript tab gives way to the setup until the project is set up"() {
    assert.strictEqual(Model.emptyText("js", { ready: false }), "")
    assert.strictEqual(Model.emptyText("js", { ready: true }), "Nothing here")
    assert.strictEqual(Model.emptyText("js", null), "Nothing here")
    assert.strictEqual(Model.emptyText("js", { ready: true }, shop({}, {}, { connectedAt: 1000 }), 1130),
      "Connected · no browser errors (last page 2 min ago)")
    assert.strictEqual(Model.emptyText("error", { ready: false }), "Nothing here")
  },
  "the whole setup guide shows until a page has reached the collector"() {
    const steps = [{ key: "url", ok: false }, { key: "helper", ok: true }, { key: "sourcemap", ok: false, optional: true }]
    assert.deepStrictEqual(Model.guideSteps({ ready: false, steps }).map(s => s.key), ["url", "helper", "sourcemap"])
    assert.deepStrictEqual(Model.guideSteps({ ready: true, steps }), [])
    assert.deepStrictEqual(Model.guideSteps(null), [])
    assert.match(Model.setupHeading("shop"), /No page of shop/)
    assert.match(Model.SETUP_HINT, /✓/)
  },
  "copy puts the problem, a blank line and its detail on the clipboard"() {
    const p = { title: "TypeError: x", sub: "AController#index", where: "app/a.rb:3" }
    assert.strictEqual(Model.copyText(p, "trace"), "TypeError: x\nAController#index\napp/a.rb:3\n\ntrace")
    assert.strictEqual(Model.copyText({ title: "T" }, ""), "T")
  },
  "details are kept per project, a newer one replaces the older, and only the last ones stay"() {
    let d = Model.keepDetail({}, { project: "shop", fp: "a", text: "one" })
    d = Model.keepDetail(d, { project: "blog", fp: "a", text: "other" })
    d = Model.keepDetail(d, { project: "shop", fp: "a", text: "two" })
    assert.strictEqual(d[Model.detailKey("shop", "a")], "two")
    assert.strictEqual(d[Model.detailKey("blog", "a")], "other")
    assert.strictEqual(Model.keepDetail({}, { project: "shop", fp: "gone", text: null })["shop/gone"], "")
    for (let i = 0; i < Model.KEPT_DETAILS + 10; i++) d = Model.keepDetail(d, { project: "shop", fp: "f" + i, text: "x" })
    assert.strictEqual(Object.keys(d).length, Model.KEPT_DETAILS)
    assert.ok(d["shop/f" + (Model.KEPT_DETAILS + 9)] !== undefined)
    assert.strictEqual(d["shop/a"], undefined)
  },
  "feed lines are the status, a detail or a setup; anything else is skipped"() {
    assert.deepStrictEqual(Model.parseLine('{"projects":[]}'), { status: { projects: [] } })
    assert.deepStrictEqual(Model.parseLine('{"detail":{"project":"p","fp":"f","text":"t"}}'), { detail: { project: "p", fp: "f", text: "t" } })
    assert.deepStrictEqual(Model.parseLine('{"setup":{"project":"p","ready":true,"steps":[]}}'), { setup: { project: "p", ready: true, steps: [] } })
    assert.strictEqual(Model.parseLine('{"setup":"x"}'), null)
    assert.strictEqual(Model.parseLine("not json"), null)
    assert.strictEqual(Model.parseLine("[1,2]"), null)
    assert.strictEqual(Model.parseLine("null"), null)
    assert.strictEqual(Model.parseLine('{"other":1}'), null)
  },
  "the settings show the origins as one line and how many problems are ignored"() {
    assert.strictEqual(Model.originsText({ jsOrigins: ["http://a.test", "http://b.test:3000"] }), "http://a.test http://b.test:3000")
    assert.strictEqual(Model.originsText({}), "")
    assert.strictEqual(Model.ignoredText(0), "Nothing is ignored.")
    assert.strictEqual(Model.ignoredText(1), "1 problem is ignored and never shown.")
    assert.strictEqual(Model.ignoredText(3), "3 problems are ignored and never shown.")
  },
  "ignored problems are listed one by one, naming the project only when there are several"() {
    const shopIgnored = shop({}, {}, { ignored: [{ fp: "a", kind: "n1", title: "Post Load ×5", where: "app/a.rb:3" }, { fp: "b" }] })
    assert.deepStrictEqual(Model.ignoredRows([shopIgnored]), [
      { project: "shop", fp: "a", title: "Post Load ×5", sub: "N+1  ·  app/a.rb:3" },
      { project: "shop", fp: "b", title: "A problem ignored earlier", sub: "" }])
    const blog = Object.assign(shop({}, {}, { ignored: [{ fp: "c", kind: "js", title: "x" }] }), { name: "blog" })
    assert.strictEqual(Model.ignoredRows([shopIgnored, blog])[2].sub, "JavaScript  ·  blog")
    assert.deepStrictEqual(Model.ignoredRows(null), [])
  },
  "the number settings match what the feed accepts"() {
    const ranges = fs.readFileSync(path.join(root, "lib", "rails_inspector", "config.py"), "utf8")
    for (const s of Model.THRESHOLD_SETTINGS.concat([Model.PORT_SETTING]))
      assert.match(ranges, new RegExp(`"${s.key}": \\(${s.from}, ${s.to}\\)`), s.key)
  },
  "a command is one line, whatever its words hold"() {
    assert.strictEqual(Model.command(["resolve", "shop", "abc"]), "resolve shop abc\n")
    assert.strictEqual(Model.command(["project", "remove", "evil\nconfig"]), "project remove evil config\n")
  },
}

const reporter = {
  async "an uncaught error is reported with where it happened"() {
    const b = browser()
    b.win.dispatch("error", { target: b.context, message: "boom", filename: BUNDLE, lineno: 12, colno: 5,
      error: Object.assign(new Error("boom"), { name: "TypeError", stack: "TypeError: boom\n at x (" + BUNDLE + ":12:5)" }) })
    const [r] = await b.reports()
    assert.strictEqual(r.project, "shop")
    assert.strictEqual(r.type, "error")
    assert.strictEqual(r.level, "error")
    assert.strictEqual(r.message, "TypeError: boom")
    assert.deepStrictEqual([r.source, r.line, r.col], [BUNDLE, 12, 5])
    assert.match(r.stack, /application\.debug\.js:12:5/)
    assert.strictEqual(r.page, "/posts?page=2")
  },
  async "a file that does not load is reported, the reporter's own files are not"() {
    const b = browser()
    b.win.dispatch("error", { target: { tagName: "IMG", src: "http://localhost:3000/logo.png" } })
    b.win.dispatch("error", { target: { tagName: "SCRIPT", src: REPORTER } })
    const reports = await b.reports()
    assert.strictEqual(reports.length, 1)
    assert.strictEqual(reports[0].type, "resource")
    assert.strictEqual(reports[0].message, "Failed to load <img> /logo.png")
  },
  async "a promise nobody caught is reported, an Error or not"() {
    const b = browser()
    b.win.dispatch("unhandledrejection", { reason: new Error("lost") })
    b.win.dispatch("unhandledrejection", { reason: { code: 7 } })
    const [a, c] = await b.reports()
    assert.strictEqual(a.message, "Error: lost")
    assert.strictEqual(c.message, 'Unhandled rejection: {"code":7}')
  },
  async "console.error is reported from the page's line, and still reaches the console"() {
    const b = browser()
    b.page('\n\nconsole.error("failed", { id: 3 }, 5)')
    const [r] = await b.reports()
    assert.strictEqual(r.type, "console")
    assert.strictEqual(r.message, 'failed {"id":3} 5')
    assert.deepStrictEqual([r.source, r.line], [BUNDLE, 3])
    assert.strictEqual(JSON.stringify(b.consoleCalls[0]), JSON.stringify(["error", ["failed", { id: 3 }, 5]]))
  },
  async "console.warn is a warning; console.assert only when it fails"() {
    const b = browser()
    b.page('console.warn("careful"); console.assert(true, "fine"); console.assert(1 === 2, "math")')
    const reports = await b.reports()
    assert.deepStrictEqual(reports.map(r => [r.type, r.level, r.message]),
      [["warning", "warning", "careful"], ["assert", "error", "Assertion failed: math"]])
    assert.strictEqual(b.consoleCalls.filter(c => c[0] === "assert").length, 2)
  },
  async "console format strings are filled in, as Stimulus reports a controller that failed"() {
    const b = browser()
    b.page('console.error("%s\\n\\n%o\\n\\n%o", "Error connecting controller", new TypeError("x is undefined"), { identifier: "cart" })')
    b.page('console.warn("%cstyled%c %d%% done", "color: red", "", 50, "extra")')
    const [stimulus, styled] = await b.reports()
    assert.strictEqual(stimulus.message.split("\n")[0], "Error connecting controller")
    assert.match(stimulus.message, /TypeError: x is undefined/)
    assert.match(stimulus.stack, /TypeError: x is undefined/)
    assert.strictEqual(styled.message, "styled 50% done extra")
  },
  async "a value that cannot be turned into JSON is still reported"() {
    const b = browser()
    b.page("var a = {}; a.self = a; console.error(a)")
    const [r] = await b.reports()
    assert.strictEqual(r.message, "[object Object]")
  },
  async "fetch: server errors are errors, client errors warnings, 422 is a form doing its job"() {
    const b = browser({ fetch: { "/boom": 500, "/missing": 404, "/form": 422, "/fine": 200 } })
    await b.context.fetch("/boom", { method: "post" })
    await b.context.fetch("/missing")
    await b.context.fetch("/form")
    await b.context.fetch("/fine")
    const reports = await b.reports()
    assert.deepStrictEqual(reports.map(r => [r.type, r.level, r.message]), [
      ["http", "error", "POST /boom → 500 Oops"],
      ["http", "warning", "GET /missing → 404 Oops"],
    ])
  },
  async "fetch: a failed connection is reported and still fails for the caller; an abort is not"() {
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" })
    const b = browser({ fetch: { "/down": new TypeError("Failed to fetch"), "/stop": abort } })
    await assert.rejects(b.context.fetch("/down"), /Failed to fetch/)
    await assert.rejects(b.context.fetch("/stop"), /aborted/)
    const reports = await b.reports()
    assert.deepStrictEqual(reports.map(r => [r.type, r.message]), [["network", "GET /down failed: TypeError: Failed to fetch"]])
  },
  async "XHR: errors and lost connections are reported, 422 is not"() {
    for (const [status, expected] of [[500, "error"], [0, "network"], [404, "warning"], [422, null], [200, null]]) {
      const b = browser({ xhrStatus: status })
      const xhr = new b.context.XMLHttpRequest()
      xhr.open("put", "/things/1")
      xhr.send()
      const reports = await b.reports()
      if (expected === null) assert.strictEqual(reports.length, 0, "status " + status)
      else assert.strictEqual(expected === "network" ? reports[0].type : reports[0].level, expected, "status " + status)
    }
  },
  async "a Content Security Policy violation is reported without pointing at the reporter"() {
    const b = browser()
    b.doc.dispatch("securitypolicyviolation", { blockedURI: "https://cdn.example/x.js", effectiveDirective: "script-src-elem",
      sourceFile: REPORTER, lineNumber: 77, columnNumber: 3, sample: "" })
    b.doc.dispatch("securitypolicyviolation", { blockedURI: "", effectiveDirective: "style-src", sourceFile: BUNDLE, lineNumber: 4 })
    const [a, c] = await b.reports()
    assert.strictEqual(a.message, "CSP blocked https://cdn.example/x.js (script-src-elem)")
    assert.strictEqual(a.source, "")
    assert.strictEqual(c.message, "CSP blocked inline (style-src)")
    assert.strictEqual(c.source, BUNDLE)
  },
  async "browser deprecations and interventions are warnings"() {
    const b = browser()
    assert.strictEqual(JSON.stringify(b.observers.reporting.opts.types), '["deprecation","intervention"]')
    b.observers.reporting.fn([{ type: "deprecation", body: { message: "unload is going away", sourceFile: BUNDLE, lineNumber: 9 } },
      { type: "intervention", body: { id: "HeavyAd" } }])
    const reports = await b.reports()
    assert.deepStrictEqual(reports.map(r => [r.type, r.level, r.message]),
      [["deprecation", "warning", "Deprecated: unload is going away"], ["intervention", "warning", "Intervention: HeavyAd"]])
  },
  async "only long tasks of at least 300ms are reported"() {
    const b = browser()
    assert.strictEqual(b.observers.performance.opts.type, "longtask")
    b.observers.performance.fn({ getEntries: () => [{ duration: 120 }, { duration: 300.4 }] })
    const reports = await b.reports()
    assert.deepStrictEqual(reports.map(r => r.message), ["Main thread blocked for 300ms"])
  },
  async "Turbo: a missing frame, a failed request and a full reload are reported"() {
    const b = browser()
    b.doc.dispatch("turbo:frame-missing", { target: { id: "comments" }, detail: { response: { status: 200 } } })
    b.doc.dispatch("turbo:fetch-request-error", { detail: { error: new TypeError("offline") } })
    b.doc.dispatch("turbo:reload", { detail: { reason: "tracked_element_mismatch" } })
    b.doc.dispatch("turbo:reload", {})
    const reports = await b.reports()
    assert.deepStrictEqual(reports.map(r => [r.level, r.message]), [
      ["error", "Turbo frame missing: #comments (response 200)"],
      ["error", "Turbo request failed: TypeError: offline"],
      ["warning", "Turbo full reload: tracked_element_mismatch"],
      ["warning", "Turbo full reload: tracked asset changed"],
    ])
  },
  async "Turbo: a stream aimed at nothing on the page is reported; refresh and found targets are not"() {
    const b = browser({ ids: ["flash", ".item"] })
    const stream = attrs => ({ target: { getAttribute: name => (name in attrs ? attrs[name] : null) } })
    b.doc.dispatch("turbo:before-stream-render", stream({ action: "replace", target: "missing" }))
    b.doc.dispatch("turbo:before-stream-render", stream({ action: "append", targets: ".gone" }))
    b.doc.dispatch("turbo:before-stream-render", stream({ action: "replace", target: "flash" }))
    b.doc.dispatch("turbo:before-stream-render", stream({ action: "update", targets: ".item" }))
    b.doc.dispatch("turbo:before-stream-render", stream({ action: "refresh" }))
    const reports = await b.reports()
    assert.deepStrictEqual(reports.map(r => r.message), [
      "Turbo stream replace: target #missing not found",
      "Turbo stream append: targets .gone not found",
    ])
  },
  async "the same report within two seconds is sent once"() {
    const b = browser()
    b.page('for (var i = 0; i < 5; i++) console.error("again")')
    b.page('console.error("other")')
    assert.deepStrictEqual((await b.reports()).map(r => r.message), ["again", "other"])
  },
  async "a page sends at most about two hundred reports"() {
    const b = browser()
    b.page('for (var i = 0; i < 500; i++) console.error("n" + i)')
    assert.strictEqual((await b.all()).length, 201)
    assert.strictEqual(b.consoleCalls.length, 500)
  },
  async "without sendBeacon the report goes by the original fetch, which reports nothing itself"() {
    const b = browser({ noBeacon: true })
    b.page('console.error("by fetch")')
    const reports = (await Promise.all(b.sent)).filter(s => s.body.type !== "hello")
    assert.strictEqual(reports.length, 1)
    assert.strictEqual(reports[0].viaFetch, true)
    assert.ok(b.fetches.every(f => f.init.mode === "no-cors"))
  },
  async "a page load says hello to the collector once, for its project"() {
    const b = browser()
    b.reload()
    const hellos = (await b.all()).filter(r => r.type === "hello")
    assert.strictEqual(hellos.length, 1)
    assert.strictEqual(hellos[0].project, "shop")
  },
  async "loading the reporter twice does not report twice"() {
    const b = browser()
    b.reload()
    b.page('console.error("once")')
    assert.strictEqual((await b.reports()).length, 1)
    assert.strictEqual(b.win.count("error"), 1)
  },
  async "when reporting itself breaks, the page and its console go on"() {
    const b = browser({ beaconThrows: true })
    b.page('console.error("still logged")')
    assert.strictEqual(JSON.stringify(b.consoleCalls), '[["error",["still logged"]]]')
    assert.strictEqual(await b.context.fetch("/fine").then(r => r.status), 200)
  },
}

async function run(name, tests) {
  let failed = 0
  for (const [title, test] of Object.entries(tests)) {
    try { await test() } catch (error) {
      failed++
      console.log("FAIL " + title + "\n  " + String(error.message).split("\n").join("\n  "))
    }
  }
  console.log(name + ": " + (failed ? failed + " failed" : "ok") + " (" + Object.keys(tests).length + ")")
  return failed
}

;(async () => {
  const failed = (await run("model", model)) + (await run("reporter", reporter))
  if (failed) process.exit(1)
})()
