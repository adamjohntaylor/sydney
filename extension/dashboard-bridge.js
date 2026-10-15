// dashboard-bridge.js - runs on the dashboard page itself (localhost:8777).
// Lets "Refresh now" start the extension's Verify run without opening the
// popup, and relays the run's progress back to the page. The page and the
// extension talk through window.postMessage: messages FROM the page carry
// source:"sydney-dashboard", messages TO the page carry source:"sydney-verifier".
//   page -> ext : {type:"ping"} | {type:"verify-start", cap, all} | {type:"verify-state"}
//   ext -> page : {type:"ready", version} | {type:"verify-state", state}
(function () {
  const VERSION = chrome.runtime.getManifest().version;
  const post = (m) => window.postMessage(Object.assign({ source: "sydney-verifier" }, m), "*");
  let timer = null;
  function stop() { if (timer) { clearInterval(timer); timer = null; } }
  function relay() {
    try {
      chrome.runtime.sendMessage({ type: "verify-state" }, (s) => {
        if (chrome.runtime.lastError || !s) { stop(); return; }
        post({ type: "verify-state", state: { running: !!s.running, total: s.total || 0, done: s.done || 0,
                                              result: s.result || null, error: s.error || null,
                                              last: (s.log && s.log.length) ? s.log[s.log.length - 1] : null } });
        if (!s.running) stop();
      });
    } catch (e) { stop(); }   // extension reloaded underneath the page
  }
  function start() { if (!timer) { relay(); timer = setInterval(relay, 1000); } }
  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data || e.data.source !== "sydney-dashboard") return;
    if (e.data.type === "ping") post({ type: "ready", version: VERSION });
    else if (e.data.type === "verify-start") {
      chrome.runtime.sendMessage({ type: "verify-start", cap: e.data.cap || 300, all: !!e.data.all },
                                 () => { void chrome.runtime.lastError; start(); });
    } else if (e.data.type === "verify-state") start();
  });
  post({ type: "ready", version: VERSION });
})();
