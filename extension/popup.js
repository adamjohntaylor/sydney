const $ = (id) => document.getElementById(id);
function render(s) {
  if (!s) return;
  $("fill").style.width = s.total ? Math.round(100 * s.done / s.total) + "%" : "0";
  $("status").textContent = s.running ? `Verifying ${s.done} / ${s.total}...`
    : s.error ? "Error: " + s.error
    : s.result ? `Done: ${s.result.changed} listing(s) changed.`
    : "Idle. Serve the dashboard (python scripts\\serve.py) first, then click Verify.";
  $("log").textContent = (s.log || []).slice().reverse().join("\n");
  $("go").disabled = !!s.running;
}
function poll() { chrome.runtime.sendMessage({ type: "verify-state" }, (s) => { if (!chrome.runtime.lastError) render(s); }); }
$("go").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "verify-start", cap: parseInt($("cap").value, 10) || 60, all: $("all").checked }, () => poll());
});
poll();
setInterval(poll, 1000);
