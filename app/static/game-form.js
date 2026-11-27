(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const status = (t) => { $("bggStatus").textContent = t; };
  const setIf = (id, v) => { if (v !== null && v !== undefined && v !== "") $(id).value = v; };

  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  async function search() {
    const q = $("bggQuery").value.trim();
    const list = $("bggResults");
    list.replaceChildren();
    if (q.length < 2) return;
    status("Searching…");
    try {
      const { results } = await getJSON(`/api/bgg/search?q=${encodeURIComponent(q)}`);
      status(results.length ? "Pick the right edition:" : "No matches.");
      for (const r of results) {
        const li = document.createElement("li");
        const b = document.createElement("button");
        b.type = "button"; b.className = "btn sm ghost";
        b.textContent = `${r.name}${r.year ? ` (${r.year})` : ""}`;
        b.onclick = () => choose(r.id);
        li.append(b);
        list.append(li);
      }
    } catch (e) { status(`Search failed: ${e.message}`); }
  }

  async function choose(id) {
    status("Loading details…");
    try {
      const g = await getJSON(`/api/bgg/thing/${id}`);
      if (!$("name").value.trim()) $("name").value = g.name || "";
      setIf("min_players", g.min_players);
      setIf("max_players", g.max_players);
      setIf("play_time_min", g.play_time_min);
      setIf("year", g.year);
      $("bgg_id").value = g.id;
      if (g.image && $("bggCover")) $("bggCover").checked = true;
      $("bggResults").replaceChildren();
      status(`Filled in from BoardGameGeek: ${g.name}${g.year ? ` (${g.year})` : ""}. Review, then save.`);
    } catch (e) { status(`Couldn't load that game: ${e.message}`); }
  }

  $("bggGo").onclick = search;
  $("bggQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } });
  const name = $("name");
  if (name && name.value && !$("bggQuery").value) $("bggQuery").value = name.value;
})();
