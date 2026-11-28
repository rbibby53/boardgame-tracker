// live.js: keeps a game night page current for everyone at the table. Re-fetches the live panel every few
// seconds (only while the page is visible), and handles taps on the "what next?" vote without a reload.
(() => {
  "use strict";
  const panel = document.getElementById("livePanel");
  if (!panel) return;
  const url = panel.dataset.url;
  const csrf = () => (document.querySelector("input[name=csrf]") || {}).value;
  const EVERY = 4000;
  let busy = false, timer = null, last = "";

  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const r = await fetch(url, { headers: { Accept: "text/html" }, cache: "no-store" });
      if (r.ok) {
        const html = await r.text();
        if (html !== last) {
          // don't yank the page out from under someone mid-tap: wait until focus leaves the panel
          if (panel.contains(document.activeElement) && document.activeElement !== document.body) return;
          last = html;
          const y = window.scrollY;
          panel.innerHTML = html;
          window.scrollTo(0, y);
        }
      }
    } catch (_) { /* offline for a moment: try again next tick */ }
    finally { busy = false; }
  }

  function start() {
    clearInterval(timer);
    if (panel.dataset.open === "1") timer = setInterval(refresh, EVERY);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  panel.addEventListener("click", async (e) => {
    const btn = e.target.closest(".vote-opt");
    if (!btn || btn.disabled) return;
    const poll = btn.closest("[data-poll]").dataset.poll;
    const on = btn.getAttribute("aria-pressed") !== "true";
    btn.disabled = true;
    try {
      const r = await fetch(`/polls/${poll}/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf(), Accept: "application/json" },
        body: JSON.stringify({ option_id: Number(btn.dataset.option), on }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Couldn't vote");
    } catch (err) {
      alert(err.message);
    } finally {
      btn.blur();
      last = "";
      await refresh();
    }
  });

  last = panel.innerHTML;
  start();
})();
