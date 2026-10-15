SNIPPET = r"""(function () {
  if (window.__railsInspector) return;
  window.__railsInspector = true;
  var P = %(project)s, U = %(url)s, sent = 0, recent = {};
  var LONG_TASK_MS = 300;
  function text(x) {
    if (x instanceof Error) return x.name + ": " + x.message;
    if (x && typeof x === "object") { try { return JSON.stringify(x); } catch (_) { } }
    return String(x);
  }
  function short(url) {
    try { var u = new URL(url, location.href); return u.origin === location.origin ? u.pathname + u.search : u.href; } catch (_) { return String(url); }
  }
  function send(type, level, message, source, line, col, stack) {
    var key = type + "|" + message + "|" + source + ":" + line;
    var now = Date.now();
    if (recent[key] && now - recent[key] < 2000) return;
    recent[key] = now;
    if (sent++ > 200) return;
    try {
      var body = JSON.stringify({ project: P, type: type, level: level, message: String(message || "").slice(0, 2000),
        source: source || "", line: line || 0, col: col || 0, stack: String(stack || "").slice(0, 4000),
        page: location.pathname + location.search });
      var blob = new Blob([body], { type: "text/plain" });
      if (!(navigator.sendBeacon && navigator.sendBeacon(U, blob)))
        originalFetch && originalFetch(U, { method: "POST", body: body, mode: "no-cors", keepalive: true });
    } catch (_) { }
  }
  function caller() {
    var frames = (new Error().stack || "").split("\n");
    for (var i = 0; i < frames.length; i++) {
      var m = frames[i].match(/(https?:\/\/[^\s)@]+):(\d+):(\d+)/);
      if (m && m[1].indexOf("/rails-inspector.js") < 0) return [m[1], +m[2], +m[3]];
    }
    return ["", 0, 0];
  }

  // Uncaught errors and failed resources (script, stylesheet, image, ...)
  window.addEventListener("error", function (e) {
    var t = e.target;
    if (t && t !== window && (t.src || t.href)) {
      var url = t.src || t.href;
      if (url.indexOf(U.replace("/report", "")) === 0) return;
      send("resource", "error", "Failed to load <" + t.tagName.toLowerCase() + "> " + short(url), url);
    } else send("error", "error", e.error ? text(e.error) : e.message, e.filename, e.lineno, e.colno, e.error && e.error.stack);
  }, true);

  // Promises nobody caught
  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    send("rejection", "error", r instanceof Error ? text(r) : "Unhandled rejection: " + text(r), "", 0, 0, r && r.stack);
  });

  // console.error / warn / assert (Stimulus reports controller errors through console.error)
  [["error", "console", "error"], ["warn", "warning", "warning"]].forEach(function (k) {
    var original = console[k[0]];
    console[k[0]] = function () {
      try {
        var args = [].slice.call(arguments), err = args.filter(function (a) { return a instanceof Error; })[0];
        var at = caller();
        send(k[1], k[2], args.map(text).join(" "), at[0], at[1], at[2], err && err.stack);
      } catch (_) { }
      return original.apply(console, arguments);
    };
  });
  var originalAssert = console.assert;
  console.assert = function (ok) {
    if (!ok) try {
      var at = caller();
      send("assert", "error", "Assertion failed: " + [].slice.call(arguments, 1).map(text).join(" "), at[0], at[1], at[2]);
    } catch (_) { }
    return originalAssert.apply(console, arguments);
  };

  // HTTP errors and network failures of fetch (Turbo, Stimulus, own code) and XHR (rails-ujs, libraries)
  var originalFetch = window.fetch;
  if (originalFetch) window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : input && input.url || String(input);
    var method = (init && init.method || input && input.method || "GET").toUpperCase();
    var p = originalFetch.apply(this, arguments);
    if (url.indexOf(U) === 0) return p;
    return p.then(function (r) {
      if (r.status >= 400 && r.status !== 422) report(method, url, r.status, r.statusText);
      return r;
    }, function (err) {
      if (!(err && err.name === "AbortError")) send("network", "error", method + " " + short(url) + " failed: " + text(err), url);
      throw err;
    });
  };
  var open = XMLHttpRequest.prototype.open, xsend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__ri = { method: String(method || "GET").toUpperCase(), url: String(url) };
    return open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    var xhr = this, info = xhr.__ri;
    if (info) {
      xhr.addEventListener("loadend", function () {
        if (xhr.status === 0) send("network", "error", info.method + " " + short(info.url) + " failed (network)", info.url);
        else if (xhr.status >= 400 && xhr.status !== 422) report(info.method, info.url, xhr.status, xhr.statusText);
      });
    }
    return xsend.apply(this, arguments);
  };
  function report(method, url, status, statusText) {
    send("http", status >= 500 ? "error" : "warning", method + " " + short(url) + " → " + status + (statusText ? " " + statusText : ""), url);
  }

  // Content Security Policy violations
  document.addEventListener("securitypolicyviolation", function (e) {
    send("csp", "error", "CSP blocked " + (e.blockedURI || "inline") + " (" + e.effectiveDirective + ")",
      /rails-inspector\.js/.test(e.sourceFile) ? "" : e.sourceFile, e.lineNumber, e.columnNumber, e.sample ? "Sample: " + e.sample : "");
  });

  // Browser deprecations and interventions
  if (window.ReportingObserver) {
    try {
      new ReportingObserver(function (reports) {
        reports.forEach(function (r) {
          var b = r.body || {};
          send(r.type, "warning", (r.type === "deprecation" ? "Deprecated: " : "Intervention: ") + (b.message || b.id || ""),
            b.sourceFile, b.lineNumber, b.columnNumber);
        });
      }, { types: ["deprecation", "intervention"], buffered: true }).observe();
    } catch (_) { }
  }

  // Long tasks blocking the main thread
  if (window.PerformanceObserver) {
    try {
      new PerformanceObserver(function (list) {
        list.getEntries().forEach(function (e) {
          if (e.duration >= LONG_TASK_MS) send("longtask", "warning", "Main thread blocked for " + Math.round(e.duration) + "ms");
        });
      }).observe({ type: "longtask", buffered: true });
    } catch (_) { }
  }

  // Tells the collector this project's pages reach it; not a problem
  send("hello", "info", "");

  // Turbo
  document.addEventListener("turbo:frame-missing", function (e) {
    send("turbo", "error", "Turbo frame missing: #" + e.target.id + " (response " + (e.detail && e.detail.response ? e.detail.response.status : "?") + ")");
  });
  document.addEventListener("turbo:fetch-request-error", function (e) {
    send("turbo", "error", "Turbo request failed: " + text(e.detail && e.detail.error));
  });
  document.addEventListener("turbo:reload", function (e) {
    send("turbo", "warning", "Turbo full reload: " + (e.detail && e.detail.reason || "tracked asset changed"));
  });
  document.addEventListener("turbo:before-stream-render", function (e) {
    var s = e.target, target = s.getAttribute("target"), targets = s.getAttribute("targets");
    var action = s.getAttribute("action");
    if (["refresh", "remove"].indexOf(action) >= 0 && !target && !targets) return;
    if (target && !document.getElementById(target))
      send("turbo", "warning", "Turbo stream " + action + ": target #" + target + " not found");
    else if (targets && !document.querySelector(targets))
      send("turbo", "warning", "Turbo stream " + action + ": targets " + targets + " not found");
  });
})();
"""
