(() => {
  "use strict";
  const root = document.getElementById("poll");
  const id = root.dataset.id;
  const csrf = document.querySelector("input[name=csrf]").value;
  let state = JSON.parse(document.getElementById("pollBoot").textContent);
  let busy = false;
  const asSel = document.getElementById("voteAs");
  const asPlayer = () => (asSel && asSel.value ? Number(asSel.value) : null);
  if (asSel) asSel.onchange = () => refresh();

  function render() {
    const list = document.getElementById("pollOptions");
    list.replaceChildren();
    const max = Math.max(1, ...state.options.map((o) => o.votes));
    for (const o of state.options) {
      const li = document.createElement("li");
      li.className = "poll-opt" + (o.mine ? " mine" : "") + (o.leading ? " leading" : "") +
        (state.winner && state.winner.id === o.game_id ? " won" : "");

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "poll-btn";
      btn.disabled = !state.open || !state.can_vote;
      btn.setAttribute("aria-pressed", o.mine);
      btn.onclick = () => toggle(o);

      const check = document.createElement("span");
      check.className = "poll-check";
      check.textContent = o.mine ? "✓" : "";
      btn.append(check);

      if (o.cover) {
        const img = document.createElement("img");
        img.className = "cover sm"; img.alt = ""; img.src = `/covers/${o.game_id}`;
        btn.append(img);
      }
      const body = document.createElement("span");
      body.className = "poll-body";
      const name = document.createElement("strong");
      name.textContent = o.name;
      const meta = document.createElement("span");
      meta.className = "small muted";
      meta.textContent = (o.voters.length ? o.voters.join(", ") : "no votes yet") +
        (o.last ? ` · last played ${o.last}` : " · never played");
      const bar = document.createElement("span");
      bar.className = "poll-bar";
      bar.style.setProperty("--w", `${(o.votes / max) * 100}%`);
      body.append(name, meta, bar);
      btn.append(body);

      const count = document.createElement("span");
      count.className = "poll-count";
      count.textContent = o.votes;
      btn.append(count);

      li.append(btn);
      list.append(li);
    }
    document.getElementById("pollVoters").textContent =
      `${state.voters} ${state.voters === 1 ? "person has" : "people have"} voted`;
  }

  async function toggle(o) {
    if (!state.open || busy) return;
    busy = true;
    o.mine = !o.mine; o.votes += o.mine ? 1 : -1;  // optimistic
    render();
    try {
      const r = await fetch(`/polls/${id}/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json" },
        body: JSON.stringify({ option_id: o.id, on: o.mine, as_player: asPlayer() }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "Vote failed");
      state = j;
    } catch (e) {
      alert(e.message);
      await refresh();
    } finally {
      busy = false;
      render();
    }
  }

  async function refresh() {
    try {
      const q = asPlayer() ? `?as_player=${asPlayer()}` : "";
      const r = await fetch(`/polls/${id}/state${q}`, { headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const was = state.open;
      state = await r.json();
      if (was && !state.open) { location.reload(); return; }
      if (!busy) render();
    } catch (_) { /* offline: try again next tick */ }
  }

  render();
  setInterval(() => { if (state.open && document.visibilityState === "visible") refresh(); }, 4000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });
})();
