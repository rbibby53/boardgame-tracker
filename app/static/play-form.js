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
    categories: Object.fromEntries(Object.entries(init.categories || {}).map(([cid, row]) => [
      Number(cid), Object.fromEntries(Object.entries(row || {}).map(([k, v]) => [Number(k), v])),
    ])),
    use_sheet: init.use_sheet ?? Object.keys(init.categories || {}).length > 0,
    winners_manual: !!init.winners_manual,
    coop_won: !!init.coop_won,
    use_rounds: init.use_rounds ?? (init.rounds || []).length > 0,
    mode: init.mode || null,   // "wins" | "points" for points games kept by round; null = game's last-used mode
    expansions: (init.expansions || []).map(Number),
    teams: (init.players || []).some((s) => s.team),
  };
  let chipFilter = "", showAllChips = false, showAllExp = false;

  const gameSel = $("game_id");
  if (init.game_id) gameSel.value = String(init.game_id);

  // ------------------------------------------------------------ device draft (survives reloads / no signal)
  const form = $("playForm");
  // Live game night: the game in progress is kept on the server so everyone at the night sees the scores.
  const LIVE = boot.live || null;
  let liveVersion = LIVE ? LIVE.version : 0, liveTimer = null, liveLost = false;
  const DRAFT_KEY = "gn-draft:" + new URL(form.action, location.href).pathname + (LIVE ? `?night=${LIVE.night_id}` : "");
  const FIELDS = ["game_id", "played_at", "location", "duration_min", "expansions", "notes"];
  let draftTimer = null;
  const store = {
    get() { try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "null"); } catch (_) { return null; } },
    set(v) { try { localStorage.setItem(DRAFT_KEY, JSON.stringify(v)); } catch (_) { /* full or blocked */ } },
    clear() { try { localStorage.removeItem(DRAFT_KEY); } catch (_) { /* blocked */ } },
  };
  let draftReady = false;  // don't save until the initial render (or restore) is done
  function saveDraft() {
    if (!draftReady) return;
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      const fields = Object.fromEntries(FIELDS.map((f) => [f, form.elements[f].value]));
      store.set({ savedAt: Date.now(), fields, state });
    }, 300);
    liveSync();
  }

  function liveSync() {
    if (!LIVE || liveLost) return;
    clearTimeout(liveTimer);
    liveTimer = setTimeout(pushLive, 700);
  }
  async function pushLive() {
    const fields = Object.fromEntries(FIELDS.map((f) => [f, form.elements[f].value]));
    try {
      const r = await fetch(`/live/${LIVE.night_id}/current`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/json" },
        body: JSON.stringify({ version: liveVersion, state, fields, summary: liveSummary() }),
      });
      if (r.status === 409) {
        const j = await r.json().catch(() => ({}));
        liveLost = true;
        $("liveLostMsg").textContent = j.reason === "taken"
          ? `${j.keeper} is keeping score now, so this phone stopped saving. `
          : "This game was stopped or the night ended, so this phone stopped saving. ";
        $("liveLost").hidden = false;
        form.querySelector("button[type=submit]").disabled = true;
        $("liveLost").scrollIntoView({ block: "center" });
      }
    } catch (_) { /* no signal: the device draft still has it; we try again on the next change */ }
  }

  // What people watching the night see: who's leading and everyone's total / rounds won so far.
  function liveSummary() {
    if (!game()) return {};
    const { w, final, winners } = preview();
    const sc = eff(), wins = byWins(), unit = roundWord().toLowerCase();
    const played = state.rounds.filter((r) => r.won.length || Object.values(r.scores).some((v) => v !== null)).length;
    const rows = state.players.map((s) => ({ pid: s.player_id, v: wins ? w[s.player_id] : (sc === "winloss" || sc === "coop" ? null : final[s.player_id]) }));
    const desc = wins || sc === "high";
    rows.sort((a, b) => (a.v === null) - (b.v === null) || (desc ? b.v - a.v : a.v - b.v));
    const leaders = rows.filter((r) => winners.has(r.pid)).map((r) => players.get(r.pid).name);
    const lead = !leaders.length ? "" : (roundsOn() && played ? `After ${played} ${unit}${played === 1 ? "" : "s"}: ` : "")
      + (leaders.length > 1 ? `${leaders.join(" & ")} tied` : `${leaders[0]} ${state.winners_manual ? "wins" : "leads"}`);
    return {
      mode: roundsOn() ? (wins ? `who won each ${unit}` : `points by ${unit}`) : sc === "winloss" ? "pick the winner" : sc === "coop" ? "co-op" : "final scores",
      lead,
      rows: rows.map((r) => ({
        name: players.get(r.pid).name, lead: winners.has(r.pid),
        value: wins ? `${r.v} ${unit}${r.v === 1 ? "" : "s"}` : r.v === null || r.v === undefined ? (winners.has(r.pid) ? "🏆" : "—") : fmt(r.v),
      })),
    };
  }
  form.addEventListener("input", saveDraft);
  form.addEventListener("change", saveDraft);
  $("draftDiscard").onclick = () => {
    if (!confirm("Throw away this unsaved play? The scores you entered will be lost.")) return;
    store.clear(); location.href = location.pathname + location.search;
  };

  function restoreDraft() {
    const d = store.get();
    if (!d || document.querySelector(".flash.err")) return false;
    if (Date.now() - d.savedAt > 7 * 864e5) { store.clear(); return false; }  // stale after a week
    for (const f of FIELDS) if (d.fields && f in d.fields) form.elements[f].value = d.fields[f];
    Object.assign(state, d.state);
    state.players = state.players.filter((s) => players.has(s.player_id));
    const when = new Date(d.savedAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    $("draftMsg").textContent = `Restored your unsaved draft from ${when}.`;
    $("draftBar").hidden = false;
    return true;
  }

  const game = () => games.get(Number(gameSel.value)) || null;
  const scoring = () => (game() ? game().scoring : "high");
  const roundWord = () => (game() ? game().round_label : "Round");
  const num = (v) => (v === "" || v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));
  const fmt = (v) => (v === null ? "" : Number.isInteger(v) ? String(v) : String(+v.toFixed(2)));

  const sheetAllowed = () => game() && ["high", "low"].includes(scoring()) && game().categories.some((c) => !c.archived || state.categories[c.id]);
  const sheetOn = () => state.use_sheet && sheetAllowed();
  const sheetCats = () => (game() ? game().categories.filter((c) => !c.archived || Object.keys(state.categories[c.id] || {}).length) : []);
  const roundsOn = () => state.use_rounds && scoring() !== "coop" && !sheetOn();
  // A points game (high/low) kept by round can be scored just by who won each round instead ("wins").
  const modeAllowed = () => roundsOn() && ["high", "low"].includes(scoring());
  const mode = () => (modeAllowed() ? (state.mode || (game() && game().last_mode) || "points") : null);
  const eff = () => (mode() === "wins" ? "winloss" : scoring());
  const byWins = () => roundsOn() && eff() === "winloss";
  const roundName = (i) => {
    const names = (game() && game().round_names) || [];
    return names[i] || `${roundWord()} ${i + 1}`;
  };

  const HINTS = {
    high: "Highest score wins.",
    low: "Lowest score wins.",
    winloss: "No points. Pick the winner, or track rounds and whoever wins the most takes it.",
    coop: "Co-op: everyone wins or loses together.",
  };

  function avatar(p) {
    const el = document.createElement("span");
    el.className = "avatar";
    el.setAttribute("aria-hidden", "true"); // decorative: the name is next to it
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
      Object.values(state.categories).forEach((row) => { delete row[id]; });
    } else {
      state.players.push({ player_id: id, score: null, winner: false, team: "" });
    }
    renderAll();
  }

  const CHIP_LIMIT = 12;
  function renderChips() {
    const box = $("playerChips");
    box.replaceChildren();
    // seated first, then whoever played most recently, then by name
    const order = [...boot.players].sort((a, b) => (seated(b.id) - seated(a.id))
      || ((b.last || "") > (a.last || "") ? 1 : (b.last || "") < (a.last || "") ? -1 : 0)
      || a.name.localeCompare(b.name));
    const many = order.length > CHIP_LIMIT;
    $("chipSearchWrap").hidden = !many;
    const q = chipFilter.trim().toLowerCase();
    const shown = order.filter((p) => seated(p.id) || (q ? p.name.toLowerCase().includes(q) : true));
    const visible = q || showAllChips || !many ? shown : shown.slice(0, Math.max(CHIP_LIMIT, state.players.length));
    for (const p of visible) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip" + (seated(p.id) ? " on" : "");
      chip.setAttribute("aria-pressed", seated(p.id));
      chip.append(avatar(p), document.createTextNode(p.name + (p.guest ? " (guest)" : "")));
      chip.onclick = () => togglePlayer(p.id);
      box.append(chip);
    }
    if (q && visible.length === state.players.length) {
      const none = document.createElement("span"); none.className = "hint"; none.textContent = `Nobody else called "${chipFilter.trim()}".`;
      box.append(none);
    }
    const more = $("chipMore");
    more.hidden = !many || !!q || showAllChips || shown.length <= visible.length;
    more.textContent = `Show everyone (${order.length})`;
    const g = game();
    const last = g && boot.last_lineup[g.id];
    $("sameAsLast").hidden = !last || !last.length;
  }
  $("chipMore").onclick = () => { showAllChips = true; renderChips(); };
  $("chipSearch").addEventListener("input", (e) => { chipFilter = e.target.value; renderChips(); });

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

  // ------------------------------------------------------------ expansions ("Playing with")
  function renderExpansions() {
    const g = game();
    const all = (g && g.expansions) || [];
    $("expWrap").hidden = !all.length;
    // with expansions listed above, the free-text box is just for house rules
    $("expTextLabel").textContent = all.length ? "House rules / other notes" : "Expansions / house rules";
    const box = $("expChips");
    box.replaceChildren();
    if (!all.length) return;
    const on = (e) => state.expansions.includes(e.id);
    const owned = all.filter((e) => e.owned || on(e));
    const others = all.filter((e) => !e.owned && !on(e));
    for (const e of showAllExp || !owned.length ? [...owned, ...others] : owned) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip" + (on(e) ? " on" : "") + (e.owned ? "" : " not-owned");
      chip.setAttribute("aria-pressed", on(e));
      chip.textContent = (on(e) ? "✓ " : "") + e.name + (e.owned ? "" : " (not ours)");
      chip.onclick = () => {
        state.expansions = on(e) ? state.expansions.filter((x) => x !== e.id) : [...state.expansions, e.id];
        renderExpansions(); saveDraft();
      };
      box.append(chip);
    }
    const more = $("expMore");
    more.hidden = showAllExp || !owned.length || !others.length;
    more.textContent = `Show the ones we don't have (${others.length})`;
  }
  $("expMore").onclick = () => { showAllExp = true; renderExpansions(); };

  // ------------------------------------------------------------ find a game (the list is long)
  const allGameOptions = gameSel.innerHTML;
  $("gameFilter").addEventListener("input", (ev) => {
    const q = ev.target.value.trim().toLowerCase();
    const keep = gameSel.value;
    gameSel.innerHTML = allGameOptions;
    if (q) {
      for (const o of [...gameSel.querySelectorAll("option")]) if (o.value && !o.textContent.toLowerCase().includes(q)) o.remove();
      for (const og of [...gameSel.querySelectorAll("optgroup")]) if (!og.children.length) og.remove();
    }
    const ids = new Set([...gameSel.options].map((o) => o.value).filter(Boolean));
    gameSel.options[0].textContent = q ? (ids.size ? `${ids.size} game${ids.size === 1 ? "" : "s"} match…` : "No games match") : "Choose a game…";
    if (ids.size === 1 && !ids.has(keep)) { gameSel.value = [...ids][0]; gameSel.dispatchEvent(new Event("change")); }
    else if (ids.has(keep)) gameSel.value = keep;
  });

  // ------------------------------------------------------------ rounds
  function renderMode() {
    const allowed = modeAllowed();
    $("modeBox").hidden = !allowed;
    if (allowed) document.querySelector(`#modeBox input[value=${mode()}]`).checked = true;
    const lower = scoring() === "low";
    document.querySelector("#modeBox input[value=wins] + span strong").textContent = `Who won each ${roundWord().toLowerCase()}`;
    $("modeHint").textContent = !roundsOn() ? "" : byWins()
      ? `Tap who won each ${roundWord().toLowerCase()}. Whoever wins the most ${roundWord().toLowerCase()}s wins the game.`
      : `Type each player's points for every ${roundWord().toLowerCase()}. ${lower ? "Lowest" : "Highest"} total wins.`;
  }
  document.querySelectorAll("#modeBox input").forEach((r) => {
    r.onchange = () => { state.mode = r.value; state.winners_manual = false; renderAll(); };
  });

  function removeRound(i) {
    const r = state.rounds[i];
    if ((Object.values(r.scores).some((v) => v !== null) || r.won.length)
        && !confirm(`Remove ${roundName(i)}? Its scores will be lost.`)) return;
    state.rounds.splice(i, 1);
    renderRounds(); refresh();
  }

  function delButton(i) {
    const db = document.createElement("button");
    db.type = "button"; db.className = "btn sm ghost round-del"; db.textContent = "✕";
    db.setAttribute("aria-label", `Remove ${roundName(i)}`);
    db.onclick = () => removeRound(i);
    return db;
  }

  function renderRounds() {
    const on = roundsOn();
    $("roundsWrap").hidden = !on;
    const table = $("roundsTable"), list = $("roundsList");
    table.replaceChildren(); list.replaceChildren();
    if (!on) return;
    if (!state.rounds.length) state.rounds.push({ label: "", scores: {}, won: [] });
    const wins = byWins();
    $("roundsGrid").hidden = wins;
    list.hidden = !wins;
    $("addRound").textContent = `+ Next ${roundWord().toLowerCase()}: ${roundName(state.rounds.length)}`;

    if (wins) {
      // one row per round: tap the player who won it
      state.rounds.forEach((r, i) => {
        const row = document.createElement("div");
        row.className = "round-row";
        const head = document.createElement("div");
        head.className = "round-head";
        const t = document.createElement("strong"); t.textContent = r.label || roundName(i);
        head.append(t, delButton(i));
        const pick = document.createElement("div");
        pick.className = "round-pick";
        pick.setAttribute("role", "group");
        pick.setAttribute("aria-label", `Who won ${r.label || roundName(i)}?`);
        for (const s of state.players) {
          const p = players.get(s.player_id);
          const btn = document.createElement("button");
          btn.type = "button";
          const on = r.won.includes(s.player_id);
          btn.className = "pick-btn" + (on ? " on" : "");
          btn.setAttribute("aria-pressed", on);
          btn.textContent = (on ? "🏆 " : "") + p.name;
          btn.onclick = () => {
            r.won = on ? [] : [s.player_id];          // one winner per round; tap again to clear
            renderRounds(); refresh();
          };
          pick.append(btn);
        }
        row.append(head, pick);
        list.append(row);
      });
      return;
    }

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
    state.rounds.forEach((r, i) => {
      const tr = tbody.insertRow();
      const lc = tr.insertCell();
      lc.className = "rl";
      lc.textContent = r.label || roundName(i);
      for (const s of state.players) {
        const inp = document.createElement("input");
        inp.type = "number"; inp.step = "any"; inp.inputMode = "decimal";
        inp.setAttribute("aria-label", `${players.get(s.player_id).name}, ${r.label || roundName(i)}`);
        inp.value = r.scores[s.player_id] ?? "";
        inp.oninput = () => { r.scores[s.player_id] = num(inp.value); refresh(); };
        tr.insertCell().append(inp);
      }
      tr.insertCell().append(delButton(i));
    });
    const tf = table.createTFoot().insertRow();
    tf.insertCell().innerHTML = "<strong>Total</strong>";
    for (const s of state.players) tf.insertCell().dataset.total = s.player_id;
    tf.insertCell();
  }

  $("addRound").onclick = () => {
    state.rounds.push({ label: "", scores: {}, won: [] });
    renderRounds(); refresh();
    if (!byWins()) {
      const rows = $("roundsTable").tBodies[0].rows;
      const first = rows[rows.length - 1].querySelector("input[type=number]");
      if (first) first.focus();
    } else {
      const rows = $("roundsList").children;
      rows[rows.length - 1].scrollIntoView({ block: "center", behavior: "smooth" });
    }
  };
  $("useRounds").onchange = (e) => { state.use_rounds = e.target.checked; renderAll(); };

  // ------------------------------------------------------------ score sheet
  function renderSheet() {
    const on = sheetOn();
    $("sheetWrap").hidden = !on;
    const table = $("sheetTable");
    table.replaceChildren();
    if (!on) return;
    const head = table.createTHead().insertRow();
    head.append(document.createElement("th"));
    for (const s of state.players) {
      const th = document.createElement("th");
      th.className = "pl";
      th.textContent = players.get(s.player_id).name;
      head.append(th);
    }
    const body = table.createTBody();
    for (const c of sheetCats()) {
      const tr = body.insertRow();
      const lc = tr.insertCell();
      lc.className = "rl";
      lc.textContent = c.name;
      if (c.archived) lc.title = "Removed from the game's sheet; kept for this play";
      for (const s of state.players) {
        const inp = document.createElement("input");
        inp.type = "number"; inp.step = "any"; inp.inputMode = "decimal";
        inp.setAttribute("aria-label", `${players.get(s.player_id).name} ${c.name}`);
        inp.value = (state.categories[c.id] || {})[s.player_id] ?? "";
        inp.oninput = () => {
          state.categories[c.id] = state.categories[c.id] || {};
          state.categories[c.id][s.player_id] = num(inp.value);
          refresh();
        };
        tr.insertCell().append(inp);
      }
    }
    const tf = table.createTFoot().insertRow();
    tf.insertCell().innerHTML = "<strong>Total</strong>";
    for (const s of state.players) tf.insertCell().dataset.sheetTotal = s.player_id;
  }
  $("useSheet").onchange = (e) => { state.use_sheet = e.target.checked; renderAll(); };

  // ------------------------------------------------------------ seats / winners
  function roundTotals() {
    const t = {}, w = {};
    for (const s of state.players) { t[s.player_id] = null; w[s.player_id] = 0; }
    if (sheetOn()) {
      for (const s of state.players) {
        for (const c of sheetCats()) {
          const v = (state.categories[c.id] || {})[s.player_id];
          if (v !== null && v !== undefined) t[s.player_id] = (t[s.player_id] ?? 0) + v;
        }
      }
      return { t, w };
    }
    if (!roundsOn()) return { t, w };
    const wins = byWins();
    for (const r of state.rounds) {
      for (const s of state.players) {
        const v = r.scores[s.player_id];
        if (!wins && v !== null && v !== undefined) t[s.player_id] = (t[s.player_id] ?? 0) + v;
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
    const sc = eff();
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

  // Final scores / winners per player. For games kept by round or on a score sheet the totals come from there,
  // so these live under "More options" (override a total, pick the winner by hand, teams).
  function renderSeats() {
    const sc = eff();
    const derived = roundsOn() || sheetOn();
    const box = $("seats");
    box.replaceChildren();
    (derived ? $("moreSeats") : $("seatsBox")).append($("seatsInner"));
    $("moreBox").hidden = sc === "coop" || !state.players.length;
    $("moreSummary").textContent = derived ? "More options: adjust totals, pick the winner, teams" : "More options: teams";
    $("coopBox").hidden = sc !== "coop";
    const pickable = sc !== "coop" && !(sc === "winloss" && !roundsOn());
    $("winnerMode").hidden = !pickable;
    $("coopWon").checked = state.coop_won;
    $("useTeams").checked = state.teams;
    document.querySelector(`#winnerMode input[value=${state.winners_manual ? "manual" : "auto"}]`).checked = true;
    $("standings").hidden = !derived || !state.players.length || sc === "coop";

    if (!state.players.length) {
      box.innerHTML = '<div class="empty">Pick who played above.</div>';
      return;
    }
    const points = sc === "high" || sc === "low";
    $("seatHint").textContent = !points ? (sc === "winloss" && !roundsOn() ? "Tap the winner." : "")
      : derived ? "Leave blank to use the total above, or type a number to replace it." : "Final scores";

    for (const s of state.players) {
      const p = players.get(s.player_id);
      const row = document.createElement("div");
      row.className = "seat" + (state.teams ? " has-team" : "");
      row.dataset.pid = s.player_id;

      const who = document.createElement("span");
      who.className = "who";
      const n = document.createElement("span"); n.className = "n"; n.textContent = p.name;
      who.append(avatar(p), n);
      row.append(who);

      const scoreWrap = document.createElement("div");
      if (points || sc === "coop") {
        const inp = document.createElement("input");
        inp.type = "number"; inp.step = "any"; inp.inputMode = "decimal";
        inp.value = s.score ?? ""; inp.setAttribute("aria-label", `${p.name} score`);
        if (sc === "coop") { inp.placeholder = "score (optional)"; inp.oninput = () => { s.score = num(inp.value); }; }
        else { inp.className = "final-score"; inp.oninput = () => { s.score = num(inp.value); refresh(); }; }
        scoreWrap.append(inp);
      }
      row.append(scoreWrap);

      if (state.teams) {
        const team = document.createElement("input");
        team.type = "text"; team.placeholder = "Team"; team.value = s.team; team.maxLength = 60;
        team.setAttribute("aria-label", `${p.name} team`);
        team.oninput = () => { s.team = team.value; };
        row.append(team);
      }

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
  $("useTeams").onchange = (e) => {
    state.teams = e.target.checked;
    if (!state.teams) state.players.forEach((s) => { s.team = ""; });
    renderSeats(); refresh();
  };

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
    saveDraft();
    const { t, w, final, winners } = preview();
    const sc = eff();
    document.querySelectorAll("#roundsTable [data-total]").forEach((td) => {
      td.textContent = fmt(t[Number(td.dataset.total)]);
    });
    document.querySelectorAll("#sheetTable [data-sheet-total]").forEach((td) => {
      td.textContent = fmt(t[Number(td.dataset.sheetTotal)]);
    });
    renderStandings(sc, final, w, winners);
    document.querySelectorAll("#seats .seat").forEach((row) => {
      const pid = Number(row.dataset.pid);
      const isW = winners.has(pid);
      row.classList.toggle("is-winner", isW);
      const inp = row.querySelector("input.final-score");
      if (inp) inp.placeholder = t[pid] !== null ? `${fmt(t[pid])} (total)` : "score";
      const wb = row.querySelector(".win-btn");
      if (sc === "coop") { wb.hidden = true; return; }
      wb.hidden = false;
      wb.textContent = isW ? "🏆 Winner" : (state.winners_manual ? "Mark winner" : "—");
      wb.classList.toggle("primary", isW && state.winners_manual);
    });
  }

  // Live "who's winning" line above the rounds / sheet, so nobody has to scroll to the totals.
  function renderStandings(sc, final, w, winners) {
    const box = $("standings");
    if (box.hidden) return;
    const wins = byWins();
    const played = state.rounds.filter((r) => r.won.length || Object.values(r.scores).some((v) => v !== null)).length;
    const val = (pid) => (wins ? w[pid] : final[pid]);
    const rows = state.players.map((s) => ({ pid: s.player_id, v: val(s.player_id) }));
    const any = rows.some((r) => r.v !== null && r.v !== undefined && (!wins || r.v > 0));
    const desc = wins || sc === "high";
    rows.sort((a, b) => (a.v === null) - (b.v === null) || (desc ? b.v - a.v : a.v - b.v));
    box.replaceChildren();
    const head = document.createElement("div");
    head.className = "standings-head";
    const unit = roundWord().toLowerCase();
    if (!any) head.textContent = sheetOn() ? "Fill in the sheet and the leader shows here." : `Scores show here after the first ${unit}.`;
    else {
      const lead = rows.filter((r) => winners.has(r.pid)).map((r) => players.get(r.pid).name);
      const done = sheetOn() ? "" : `After ${played} ${unit}${played === 1 ? "" : "s"}: `;
      head.textContent = done + (lead.length > 1 ? `${lead.join(" & ")} tied for the lead` : lead.length ? `${lead[0]} ${state.winners_manual ? "wins" : "leads"}` : "");
    }
    box.append(head);
    if (!any) return;
    const ul = document.createElement("ol");
    ul.className = "standings-list";
    for (const r of rows) {
      const li = document.createElement("li");
      li.className = winners.has(r.pid) ? "lead" : "";
      const n = document.createElement("span"); n.textContent = players.get(r.pid).name;
      const v = document.createElement("strong");
      v.textContent = wins ? `${r.v} ${unit}${r.v === 1 ? "" : "s"}` : (r.v === null || r.v === undefined ? "—" : fmt(r.v));
      li.append(n, v);
      ul.append(li);
    }
    box.append(ul);
  }

  function renderGameBits() {
    const g = game();
    const sc = scoring();
    $("scoringHint").textContent = g ? (byWins() && sc !== "winloss" ? "Most rounds won wins." : HINTS[sc]) : "";
    document.querySelectorAll("#roundWord, .rw").forEach((el) => { el.textContent = roundWord().toLowerCase(); });
    $("sheetToggleWrap").hidden = !sheetAllowed();
    $("useSheet").checked = sheetOn();
    $("roundsToggleWrap").hidden = sc === "coop" || sheetOn();
    $("useRounds").checked = state.use_rounds;
    $("scoreCard").hidden = !g;
  }

  function renderAll() {
    renderGameBits();
    renderExpansions();
    renderMode();
    renderChips();
    renderSheet();
    renderRounds();
    renderSeats();
    refresh();
  }

  gameSel.onchange = () => {
    const g = game();
    state.mode = null;  // use how this game was scored last time
    showAllExp = false;
    state.expansions = g ? (g.last_expansions || []).filter((id) => g.expansions.some((e) => e.id === id)) : [];
    if (g && !state.rounds.some((r) => Object.keys(r.scores).length || r.won.length)) {
      state.use_rounds = g.uses_rounds;
    }
    if (g) {
      state.categories = {};
      state.use_sheet = g.categories.some((c) => !c.archived);
    }
    if (g && g.scoring === "winloss" && !state.use_rounds) state.winners_manual = true;
    renderAll();
  };

  $("playForm").addEventListener("submit", (e) => {
    const g = game();
    if (!g) { e.preventDefault(); alert("Pick a game."); return; }
    if (!state.players.length) { e.preventDefault(); alert("Pick who played."); return; }
    const sc = eff();
    const useR = roundsOn();
    const useS = sheetOn();
    const ids = new Set(state.players.map((s) => s.player_id));
    // In points mode the round winner is whoever had the best score that round (lowest for low-score games).
    const autoWon = (scores) => {
      const vals = Object.values(scores);
      if (!vals.length || !["high", "low"].includes(sc)) return [];
      const best = sc === "low" ? Math.min(...vals) : Math.max(...vals);
      return Object.entries(scores).filter(([, v]) => v === best).map(([k]) => Number(k));
    };
    const payload = {
      players: state.players.map((s) => ({
        player_id: s.player_id,
        score: sc === "winloss" ? null : s.score,
        winner: state.winners_manual ? s.winner : false,
        team: state.teams ? (s.team || null) : null,
      })),
      rounds: useR
        ? state.rounds
            .map((r, i) => {
              const scores = Object.fromEntries(Object.entries(r.scores).filter(([k, v]) => ids.has(Number(k)) && v !== null && sc !== "winloss"));
              return {
                label: r.label || roundName(i),
                scores,
                won: sc === "winloss" ? r.won.filter((w) => ids.has(w)) : autoWon(scores),
              };
            })
            .filter((r) => Object.keys(r.scores).length || r.won.length)  // untouched rounds are dropped
        : [],
      mode: mode(),
      expansions: state.expansions.filter((id) => (g.expansions || []).some((e) => e.id === id)),
      categories: useS
        ? Object.fromEntries(sheetCats().map((c) => [c.id, Object.fromEntries(
            Object.entries(state.categories[c.id] || {}).filter(([k, v]) => ids.has(Number(k)) && v !== null))]))
        : {},
      winners_manual: sc !== "coop" && state.winners_manual,
      coop_won: sc === "coop" ? state.coop_won : null,
    };
    if (sc === "winloss" && !payload.winners_manual && !payload.rounds.some((r) => r.won.length)) {
      e.preventDefault();
      alert(useR ? `Tap who won each ${roundWord().toLowerCase()} before saving.` : "Tap who won before saving.");
      return;
    }
    $("payload").value = JSON.stringify(payload);
    if (!navigator.onLine) {
      e.preventDefault();
      draftReady = true; saveDraft();
      alert("You're offline. This play is saved on this device. Open Log a play again once you're back online and tap Save.");
      return;
    }
    store.clear();
  });

  if (LIVE && LIVE.fields) {  // whatever the previous scorekeeper filled in (where, notes…)
    for (const f of FIELDS) if (f !== "game_id" && LIVE.fields[f]) form.elements[f].value = LIVE.fields[f];
  }
  const restored = LIVE ? false : restoreDraft();  // live: the server copy is the one that counts
  if (!restored && gameSel.value && init.use_rounds === undefined && !state.rounds.length) {
    const g = game();
    if (g) state.use_rounds = g.uses_rounds;
    if (g && init.use_sheet === undefined) state.use_sheet = g.categories.some((c) => !c.archived);
    if (g && g.scoring === "winloss" && !state.use_rounds) state.winners_manual = true;
  }
  renderAll();
  draftReady = true;
})();
