(() => {
  "use strict";
  const boot = JSON.parse(document.getElementById("boot").textContent);
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector("input[name=csrf]").value;

  const players = new Map(boot.players.map((p) => [p.id, p]));
  const games = new Map(boot.games.map((g) => [g.id, g]));

  const init = boot.state || {};
  const state = {
    players: (init.players || []).map((s) => ({
      player_id: Number(s.player_id),
      score: s.score ?? null,
      winner: !!s.winner,
      team: s.team || "",
    })).filter((s) => players.has(s.player_id)),
    rounds: (init.rounds || []).map((r) => ({
      label: r.label || "",
      scores: Object.fromEntries(Object.entries(r.scores || {}).map(([k, v]) => [Number(k), v])),
      won: (r.won || []).map(Number),
    })),
    winners_manual: !!init.winners_manual,
    coop_won: !!init.coop_won,
    use_rounds: init.use_rounds ?? (init.rounds || []).length > 0,
  };

  const gameSel = $("game_id");
  if (init.game_id) gameSel.value = String(init.game_id);

  const game = () => games.get(Number(gameSel.value)) || null;
  const scoring = () => (game() ? game().scoring : "high");
  const roundWord = () => (game() ? game().round_label : "Round");
  const num = (v) => (v === "" || v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));
  const fmt = (v) => (v === null ? "" : Number.isInteger(v) ? String(v) : String(+v.toFixed(2)));

  const HINTS = {
    high: "Highest score wins.",
    low: "Lowest score wins.",
    winloss: "No points. Pick the winner, or track rounds and whoever wins the most takes it.",
    coop: "Co-op: everyone wins or loses together.",
  };

  function avatar(p) {
    const el = document.createElement("span");
    el.className = "avatar";
    el.style.setProperty("--c", p.color);
    el.textContent = p.initials;
    if (p.avatar) {
      const img = document.createElement("img");
      img.src = p.avatar; img.alt = ""; img.referrerPolicy = "no-referrer";
      img.onerror = () => img.remove();
      el.prepend(img);
    }
    return el;
  }

  // ------------------------------------------------------------ players
  function seated(id) { return state.players.some((s) => s.player_id === id); }

  function togglePlayer(id) {
    if (seated(id)) {
      state.players = state.players.filter((s) => s.player_id !== id);
      state.rounds.forEach((r) => { delete r.scores[id]; r.won = r.won.filter((w) => w !== id); });
    } else {
      state.players.push({ player_id: id, score: null, winner: false, team: "" });
    }
    renderAll();
  }

  function renderChips() {
    const box = $("playerChips");
    box.replaceChildren();
    const order = [...boot.players].sort((a, b) => seated(b.id) - seated(a.id));
    for (const p of order) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip" + (seated(p.id) ? " on" : "");
      chip.setAttribute("aria-pressed", seated(p.id));
      chip.append(avatar(p), document.createTextNode(p.name + (p.guest ? " (guest)" : "")));
      chip.onclick = () => togglePlayer(p.id);
      box.append(chip);
    }
    const g = game();
    const last = g && boot.last_lineup[g.id];
    $("sameAsLast").hidden = !last || !last.length;
  }

  $("sameAsLast").onclick = () => {
    const g = game();
    const last = (g && boot.last_lineup[g.id]) || [];
    for (const id of last) if (players.has(id) && !seated(id)) state.players.push({ player_id: id, score: null, winner: false, team: "" });
    renderAll();
  };

  async function addGuest() {
    const input = $("guestName");
    const name = input.value.trim();
    if (!name) return;
    const btn = $("addGuest");
    btn.disabled = true;
    try {
      const r = await fetch("/players", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json" },
        body: JSON.stringify({ name }),
      });
      const p = await r.json();
      if (!r.ok) throw new Error(p.error || "Could not add player");
      if (!players.has(p.id)) { players.set(p.id, p); boot.players.push(p); }
      if (!seated(p.id)) state.players.push({ player_id: p.id, score: null, winner: false, team: "" });
      input.value = "";
      renderAll();
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
    }
  }
  $("addGuest").onclick = addGuest;
  $("guestName").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addGuest(); } });

  // ------------------------------------------------------------ rounds
  function renderRounds() {
    const on = state.use_rounds && scoring() !== "coop";
    $("roundsWrap").hidden = !on;
    const table = $("roundsTable");
    table.replaceChildren();
    if (!on) return;
    if (!state.rounds.length) state.rounds.push({ label: "", scores: {}, won: [] });

    const thead = table.createTHead().insertRow();
    thead.append(document.createElement("th"));
    for (const s of state.players) {
      const th = document.createElement("th");
      th.className = "pl";
      th.textContent = players.get(s.player_id).name;
      thead.append(th);
    }
    thead.append(document.createElement("th"));

    const tbody = table.createTBody();
    const showPoints = scoring() !== "winloss";
    state.rounds.forEach((r, i) => {
      const tr = tbody.insertRow();
      const lc = tr.insertCell();
      lc.className = "rl";
      const li = document.createElement("input");
      li.type = "text"; li.value = r.label; li.placeholder = `${roundWord()} ${i + 1}`; li.maxLength = 100;
      li.oninput = () => { r.label = li.value; };
      lc.append(li);
      for (const s of state.players) {
        const td = tr.insertCell();
        const cell = document.createElement("div");
        cell.className = "cell";
        if (showPoints) {
          const inp = document.createElement("input");
          inp.type = "number"; inp.step = "any"; inp.inputMode = "decimal";
          inp.value = r.scores[s.player_id] ?? "";
          inp.oninput = () => { r.scores[s.player_id] = num(inp.value); refresh(); };
          cell.append(inp);
        }
        const wl = document.createElement("label");
        wl.className = "won-toggle";
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.checked = r.won.includes(s.player_id);
        cb.onchange = () => {
          r.won = cb.checked ? [...new Set([...r.won, s.player_id])] : r.won.filter((w) => w !== s.player_id);
          refresh();
        };
        wl.append(cb, document.createTextNode("won"));
        cell.append(wl);
        td.append(cell);
      }
      const del = tr.insertCell();
      const db = document.createElement("button");
      db.type = "button"; db.className = "btn sm ghost"; db.textContent = "✕"; db.title = "Remove";
      db.onclick = () => { state.rounds.splice(i, 1); renderRounds(); refresh(); };
      del.append(db);
    });

    const tf = table.createTFoot().insertRow();
    tf.insertCell().innerHTML = "<strong>Total</strong>";
    for (const s of state.players) {
      const td = tf.insertCell();
      td.dataset.total = s.player_id;
    }
    tf.insertCell();
  }

  $("addRound").onclick = () => {
    state.rounds.push({ label: "", scores: {}, won: [] });
    renderRounds(); refresh();
    const rows = $("roundsTable").tBodies[0].rows;
    const first = rows[rows.length - 1].querySelector("input[type=number]");
    if (first) first.focus();
  };
  $("useRounds").onchange = (e) => { state.use_rounds = e.target.checked; renderRounds(); renderSeats(); refresh(); };

  // ------------------------------------------------------------ seats / winners
  function roundTotals() {
    const t = {}, w = {};
    for (const s of state.players) { t[s.player_id] = null; w[s.player_id] = 0; }
    if (!(state.use_rounds && scoring() !== "coop")) return { t, w };
    for (const r of state.rounds) {
      for (const s of state.players) {
        const v = r.scores[s.player_id];
        if (v !== null && v !== undefined) t[s.player_id] = (t[s.player_id] ?? 0) + v;
        if (r.won.includes(s.player_id)) w[s.player_id] += 1;
      }
    }
    return { t, w };
  }

  function preview() {
    const { t, w } = roundTotals();
    const final = {};
    for (const s of state.players) final[s.player_id] = s.score ?? t[s.player_id];
    let winners = new Set();
    const sc = scoring();
    if (sc === "coop") {
      if (state.coop_won) winners = new Set(state.players.map((s) => s.player_id));
    } else if (state.winners_manual) {
      winners = new Set(state.players.filter((s) => s.winner).map((s) => s.player_id));
    } else if (sc === "high" || sc === "low") {
      const vals = Object.values(final).filter((v) => v !== null);
      if (vals.length) {
        const best = sc === "high" ? Math.max(...vals) : Math.min(...vals);
        for (const [pid, v] of Object.entries(final)) if (v === best) winners.add(Number(pid));
      }
    } else if (sc === "winloss") {
      const best = Math.max(0, ...Object.values(w));
      if (best > 0) for (const [pid, v] of Object.entries(w)) if (v === best) winners.add(Number(pid));
    }
    return { t, w, final, winners };
  }

  function renderSeats() {
    const sc = scoring();
    const box = $("seats");
    box.replaceChildren();
    $("coopBox").hidden = sc !== "coop";
    $("winnerMode").hidden = sc === "coop";
    $("coopWon").checked = state.coop_won;
    document.querySelector(`#winnerMode input[value=${state.winners_manual ? "manual" : "auto"}]`).checked = true;

    if (!state.players.length) {
      box.innerHTML = '<div class="empty">Pick who played above.</div>';
      return;
    }
    const points = sc !== "winloss";
    $("seatHint").textContent = points
      ? (state.use_rounds ? "Final score defaults to the round total. Type a number to override it." : "Final scores")
      : "";

    for (const s of state.players) {
      const p = players.get(s.player_id);
      const row = document.createElement("div");
      row.className = "seat";
      row.dataset.pid = s.player_id;

      const who = document.createElement("span");
      who.className = "who";
      const n = document.createElement("span"); n.className = "n"; n.textContent = p.name;
      who.append(avatar(p), n);
      row.append(who);

      const scoreWrap = document.createElement("div");
      if (points && sc !== "coop") {
        const inp = document.createElement("input");
        inp.type = "number"; inp.step = "any"; inp.inputMode = "decimal";
        inp.value = s.score ?? ""; inp.setAttribute("aria-label", `${p.name} score`);
        inp.className = "final-score";
        inp.oninput = () => { s.score = num(inp.value); refresh(); };
        scoreWrap.append(inp);
      } else if (sc === "coop") {
        const inp = document.createElement("input");
        inp.type = "number"; inp.step = "any"; inp.inputMode = "decimal"; inp.placeholder = "score (optional)";
        inp.value = s.score ?? "";
        inp.oninput = () => { s.score = num(inp.value); };
        scoreWrap.append(inp);
      }
      row.append(scoreWrap);

      const team = document.createElement("input");
      team.type = "text"; team.placeholder = "Team"; team.value = s.team; team.maxLength = 60;
      team.oninput = () => { s.team = team.value; };
      row.append(team);

      const wb = document.createElement("button");
      wb.type = "button"; wb.className = "btn sm win-btn";
      wb.onclick = () => {
        if (sc === "coop") return;
        if (!state.winners_manual) {
          // switching to manual: seed with the current auto result
          const { winners } = preview();
          state.players.forEach((x) => { x.winner = winners.has(x.player_id); });
          state.winners_manual = true;
          document.querySelector("#winnerMode input[value=manual]").checked = true;
        }
        s.winner = !s.winner;
        refresh();
      };
      row.append(wb);
      box.append(row);
    }
  }

  document.querySelectorAll("#winnerMode input").forEach((r) => {
    r.onchange = () => {
      state.winners_manual = r.value === "manual" && r.checked;
      if (state.winners_manual) {
        const { winners } = preview();
        state.players.forEach((x) => { x.winner = winners.has(x.player_id); });
      }
      refresh();
    };
  });
  $("coopWon").onchange = (e) => { state.coop_won = e.target.checked; refresh(); };

  function refresh() {
    const { t, w, winners } = preview();
    const sc = scoring();
    document.querySelectorAll("#roundsTable [data-total]").forEach((td) => {
      const pid = Number(td.dataset.total);
      td.textContent = sc === "winloss" ? `${w[pid]} won` : fmt(t[pid]) + (w[pid] ? ` · ${w[pid]}★` : "");
    });
    document.querySelectorAll("#seats .seat").forEach((row) => {
      const pid = Number(row.dataset.pid);
      const isW = winners.has(pid);
      row.classList.toggle("is-winner", isW);
      const inp = row.querySelector("input.final-score");
      if (inp) inp.placeholder = t[pid] !== null ? `${fmt(t[pid])} (from ${roundWord().toLowerCase()}s)` : "score";
      const wb = row.querySelector(".win-btn");
      if (sc === "coop") { wb.hidden = true; return; }
      wb.hidden = false;
      wb.textContent = isW ? "🏆 Winner" : (state.winners_manual ? "Mark winner" : "—");
      wb.classList.toggle("primary", isW && state.winners_manual);
    });
  }

  function renderGameBits() {
    const g = game();
    const sc = scoring();
    $("scoringHint").textContent = g ? HINTS[sc] : "";
    document.querySelectorAll("#roundWord, .rw").forEach((el) => { el.textContent = roundWord().toLowerCase(); });
    $("roundsToggleWrap").hidden = sc === "coop";
    $("useRounds").checked = state.use_rounds;
    $("scoreCard").hidden = !g;
  }

  function renderAll() {
    renderGameBits();
    renderChips();
    renderRounds();
    renderSeats();
    refresh();
  }

  gameSel.onchange = () => {
    const g = game();
    if (g && !state.rounds.some((r) => Object.keys(r.scores).length || r.won.length)) {
      state.use_rounds = g.uses_rounds;
    }
    if (g && g.scoring === "winloss" && !state.use_rounds) state.winners_manual = true;
    renderAll();
  };

  $("playForm").addEventListener("submit", (e) => {
    const g = game();
    if (!g) { e.preventDefault(); alert("Pick a game."); return; }
    if (!state.players.length) { e.preventDefault(); alert("Pick who played."); return; }
    const sc = g.scoring;
    const useR = state.use_rounds && sc !== "coop";
    const ids = new Set(state.players.map((s) => s.player_id));
    const payload = {
      players: state.players.map((s) => ({
        player_id: s.player_id,
        score: sc === "winloss" ? null : s.score,
        winner: state.winners_manual ? s.winner : false,
        team: s.team || null,
      })),
      rounds: useR
        ? state.rounds
            .map((r) => ({
              label: r.label || null,
              scores: Object.fromEntries(Object.entries(r.scores).filter(([k, v]) => ids.has(Number(k)) && v !== null && sc !== "winloss")),
              won: r.won.filter((w) => ids.has(w)),
            }))
            .filter((r) => r.label || Object.keys(r.scores).length || r.won.length)
        : [],
      winners_manual: sc !== "coop" && state.winners_manual,
      coop_won: sc === "coop" ? state.coop_won : null,
    };
    if (sc === "winloss" && !payload.winners_manual && !payload.rounds.some((r) => r.won.length)) {
      e.preventDefault();
      alert("Mark who won (or the round winners) before saving.");
      return;
    }
    $("payload").value = JSON.stringify(payload);
  });

  if (gameSel.value && init.use_rounds === undefined && !state.rounds.length) {
    const g = game();
    if (g) state.use_rounds = g.uses_rounds;
    if (g && g.scoring === "winloss" && !state.use_rounds) state.winners_manual = true;
  }
  renderAll();
})();
