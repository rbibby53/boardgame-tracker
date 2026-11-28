window.GN = (() => {
  "use strict";
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function tab(group, key) {
    document.querySelectorAll(`[data-tab="${group}"]`).forEach((el) => { el.hidden = el.dataset.key !== key; });
  }

  function lineChart(id, kind) {
    const el = document.getElementById(id);
    const dataEl = document.getElementById(id + "-data");
    if (!el || !dataEl || !window.Chart) return;
    const series = JSON.parse(dataEl.textContent);
    const cumulative = kind === "cumulative";
    const text = css("--muted"), grid = css("--border");

    const datasets = series.map((s) => ({
      label: s.label,
      data: s.data.map((d) => ({ x: Date.parse(d.x), y: d.y, play: d.play, win: d.win })),
      borderColor: s.color,
      backgroundColor: s.color,
      stepped: cumulative ? "before" : false,
      tension: cumulative ? 0 : 0.25,
      pointRadius: (ctx) => (ctx.raw && ctx.raw.win ? 5 : 3),
      pointStyle: (ctx) => (ctx.raw && ctx.raw.win ? "star" : "circle"),
      borderWidth: 2,
    }));

    const xs = datasets.flatMap((d) => d.data.map((p) => p.x));
    let xMin = Math.min(...xs), xMax = Math.max(...xs);
    const pad = xMax - xMin < 864e5 ? 864e5 : (xMax - xMin) * 0.03;
    xMin -= pad; xMax += pad;

    new Chart(el, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: false },
        scales: {
          x: {
            type: "linear",
            min: xMin,
            max: xMax,
            ticks: { color: text, maxTicksLimit: 8, stepSize: xMax - xMin < 10 * 864e5 ? 864e5 : undefined, callback: (v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "2-digit" }) },
            grid: { color: grid },
          },
          y: { ticks: { color: text, precision: 0 }, grid: { color: grid }, beginAtZero: cumulative },
        },
        plugins: {
          legend: { labels: { color: text, usePointStyle: true, boxWidth: 8 } },
          tooltip: {
            callbacks: {
              title: (items) => new Date(items[0].parsed.x).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }),
              label: (item) => `${item.dataset.label}: ${item.parsed.y}${cumulative ? " wins" : ""}${item.raw.win && !cumulative ? " 🏆" : ""}`,
            },
          },
        },
        onClick: (_e, els) => {
          if (!els.length) return;
          const raw = datasets[els[0].datasetIndex].data[els[0].index];
          if (raw && raw.play) location.href = `/plays/${raw.play}`;
        },
      },
    });
  }

  function forget() {
    try {
      Object.keys(localStorage).filter((k) => k.startsWith("gn-draft:")).forEach((k) => localStorage.removeItem(k));
    } catch (_) { /* storage blocked */ }
    if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage("clear");
    if (window.caches) caches.keys().then((ks) => ks.forEach((k) => caches.delete(k)));
  }

  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {}));
  }

  function netBadge() {
    let el = document.getElementById("offlineBadge");
    if (navigator.onLine) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement("div");
      el.id = "offlineBadge";
      el.className = "offline-badge";
      el.textContent = "Offline: changes are saved on this device until you reconnect";
      document.body.prepend(el);
    }
  }
  window.addEventListener("online", netBadge);
  window.addEventListener("offline", netBadge);
  document.addEventListener("DOMContentLoaded", netBadge);
  // On phones the tab row scrolls sideways: bring the current page's tab into view.
  document.addEventListener("DOMContentLoaded", () => {
    const on = document.querySelector(".nav a.on"), nav = on && on.parentElement;
    if (nav && nav.scrollWidth > nav.clientWidth) {
      nav.scrollLeft += on.getBoundingClientRect().left - nav.getBoundingClientRect().left
        - (nav.clientWidth - on.offsetWidth) / 2;
    }
  });

  return { tab, lineChart, forget };
})();
