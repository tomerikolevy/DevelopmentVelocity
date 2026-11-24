(() => {
  const STATIC = window.STATIC_DATA || null;
  const $ = s => document.querySelector(s);
  const esc = Charts.esc;
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const MAX_REPOS = 7; // 8th colour is reserved for "Other"
  const state = { data: null, gran: "month", sort: { k: "merged_at", dir: -1 } };

  if (STATIC) document.querySelectorAll(".live-only").forEach(n => n.classList.add("hidden"));

  // ---- Periods ----------------------------------------------------------
  function weekStart(d) {
    const x = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 6) % 7));
    return x;
  }
  function periodKey(iso, gran) {
    const d = new Date(iso);
    if (gran === "month") return iso.slice(0, 7);
    return weekStart(d).toISOString().slice(0, 10);
  }
  function periodRange(fromIso, toIso, gran) {
    const out = [];
    if (gran === "month") {
      let [y, m] = fromIso.slice(0, 7).split("-").map(Number);
      const end = toIso.slice(0, 7);
      while (true) {
        const k = `${y}-${String(m).padStart(2, "0")}`;
        out.push(k);
        if (k >= end || out.length > 600) break;
        m++; if (m > 12) { m = 1; y++; }
      }
    } else {
      let d = weekStart(new Date(fromIso));
      const end = weekStart(new Date(toIso)).getTime();
      while (d.getTime() <= end && out.length < 1200) { out.push(d.toISOString().slice(0, 10)); d.setUTCDate(d.getUTCDate() + 7); }
    }
    return out;
  }
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function shortLabel(k, gran) {
    if (gran === "month") { const [y, m] = k.split("-"); return `${MONTHS[+m - 1]} ${y.slice(2)}`; }
    const [, m, d] = k.split("-"); return `${MONTHS[+m - 1]} ${+d}`;
  }
  function longLabel(k, gran) {
    if (gran === "month") { const [y, m] = k.split("-"); return `${MONTHS[+m - 1]} ${y}`; }
    return `Week of ${shortLabel(k, gran)}, ${k.slice(0, 4)}`;
  }

  // ---- Filtering -----------------------------------------------------------
  function filtered() {
    const from = $("#f-from").value, to = $("#f-to").value, repo = $("#f-repo").value;
    return state.data.prs.filter(p => p.merged_at && p.score != null
      && (!from || p.merged_at.slice(0, 10) >= from)
      && (!to || p.merged_at.slice(0, 10) <= to)
      && (!repo || p.repo === repo));
  }

  function render() {
    const prs = filtered();
    const gran = state.gran;
    const from = $("#f-from").value || (prs.length ? prs.map(p => p.merged_at).sort()[0] : new Date().toISOString());
    const to = $("#f-to").value || new Date().toISOString();
    const periods = periodRange(from, to, gran);
    const idx = new Map(periods.map((k, i) => [k, i]));

    // Repos ranked by total units; top 7 keep their own colour, the rest fold into Other.
    const repoTotals = {};
    prs.forEach(p => repoTotals[p.repo] = (repoTotals[p.repo] || 0) + p.score);
    const ranked = Object.keys(repoTotals).sort((a, b) => repoTotals[b] - repoTotals[a]);
    const allRepos = [...new Set(state.data.prs.map(p => p.repo))].sort();
    const colorOf = {};  // colour follows the repo (stable across filters), by overall rank
    const overall = {};
    state.data.prs.forEach(p => overall[p.repo] = (overall[p.repo] || 0) + (p.score || 0));
    Object.keys(overall).sort((a, b) => overall[b] - overall[a]).forEach((r, i) => colorOf[r] = i < MAX_REPOS ? css(`--series-${i + 1}`) : null);
    const named = ranked.filter(r => colorOf[r]).slice(0, MAX_REPOS);
    const seriesMap = new Map(named.map(r => [r, Array(periods.length).fill(0)]));
    const other = Array(periods.length).fill(0);
    const count = Array(periods.length).fill(0);
    prs.forEach(p => {
      const i = idx.get(periodKey(p.merged_at, gran));
      if (i == null) return;
      (seriesMap.get(p.repo) || other)[i] += p.score;
      count[i]++;
    });
    const series = named.map(r => ({ name: r.split("/")[1] || r, color: colorOf[r], values: seriesMap.get(r) }));
    if (other.some(v => v)) series.push({ name: "Other repos", color: css("--series-8"), values: other });
    const totals = periods.map((_, i) => series.reduce((s, x) => s + x.values[i], 0));
    const rolling = totals.map((_, i) => { const w = totals.slice(Math.max(0, i - 3), i + 1); return w.reduce((a, b) => a + b, 0) / w.length; });
    const labels = periods.map(k => shortLabel(k, gran));
    const tipTitle = (_, i) => longLabel(periods[i], gran);

    Charts.stackedBars($("#c-units"), { labels, series, line: { name: "Rolling 4-period avg", color: css("--text-primary"), values: rolling }, tipTitle });
    Charts.bars($("#c-count"), { labels, values: count, color: css("--series-1"), name: "PRs", tipTitle });
    Charts.lineChart($("#c-avg"), { labels, values: periods.map((_, i) => count[i] ? totals[i] / count[i] : null), color: css("--series-1"), name: "Avg score", tipTitle });
    const dist = Array(10).fill(0); prs.forEach(p => dist[p.score - 1]++);
    Charts.bars($("#c-dist"), { labels: dist.map((_, i) => String(i + 1)), values: dist, color: css("--series-1"), name: "PRs", tipTitle: l => `Score ${l}` });

    renderTiles(prs, totals, gran);
    renderTable(prs);
  }

  function renderTiles(prs, totals, gran) {
    const units = prs.reduce((s, p) => s + p.score, 0);
    const n = prs.length;
    const unit = gran === "month" ? "month" : "week";
    const last = totals.slice(-3), prev = totals.slice(-6, -3);
    const sum = a => a.reduce((x, y) => x + y, 0);
    const change = sum(prev) ? Math.round((sum(last) - sum(prev)) / sum(prev) * 100) : null;
    const tiles = [
      ["Complexity units", units, `${n} merged PRs`],
      ["Average per PR", n ? (units / n).toFixed(1) : "—", "score 1–10"],
      [`Units per ${unit}`, totals.length ? (units / totals.length).toFixed(1) : "—", `over ${totals.length} ${unit}s`],
      [`Last 3 ${unit}s`, sum(last), change == null ? "no earlier period" : `${change >= 0 ? "+" : ""}${change}% vs previous 3`],
    ];
    $("#tiles").innerHTML = tiles.map(([l, v, d]) => `<div class="card tile"><div class="label">${l}</div><div class="value">${v}</div><div class="delta">${d}</div></div>`).join("");
  }

  function renderTable(prs) {
    const { k, dir } = state.sort;
    const rows = [...prs].sort((a, b) => (a[k] ?? "") < (b[k] ?? "") ? -dir : (a[k] ?? "") > (b[k] ?? "") ? dir : 0);
    const d = p => p.dimensions ? `scope ${p.dimensions.scope} · logic ${p.dimensions.logic_depth} · novelty ${p.dimensions.novelty} · risk ${p.dimensions.risk}` : "";
    $("#pr-table tbody").innerHTML = rows.map(p => `<tr>
      <td>${p.merged_at.slice(0, 10)}</td>
      <td>${esc(p.repo.split("/")[1] || p.repo)}</td>
      <td><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a> <span class="muted">#${p.number}</span></td>
      <td class="num"><span class="score-pill">${p.score}</span>${p.score_source === "manual" && p.llm_score != null && p.llm_score !== p.score ? `<div class="muted" title="LLM score">LLM ${p.llm_score}</div>` : ""}</td>
      <td class="dims">${d(p)}</td>
      <td>${esc(p.rationale || "")}</td>
      <td><span class="tag ${p.score_source === "manual" ? "manual" : ""}">${p.score_source}</span></td>
      <td class="num">${p.rubric_version ? "v" + p.rubric_version : "—"}</td></tr>`).join("");
    $("#table-sub").textContent = `${rows.length} PRs in the current filter. Click a column to sort.`;
  }

  // ---- Rescore & labels (live server only) ---------------------------------
  async function api(path, opts) {
    const r = await fetch(path, opts);
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }
  function renderBanners() {
    const d = state.data;
    if (STATIC) return;
    const parts = [];
    if (d.outdated) parts.push(`${d.outdated} PRs were scored with an older rubric (current: v${d.rubric_version}).`);
    if (d.unscored) parts.push(`${d.unscored} PRs have not been scored yet.`);
    $("#rescore-banner").classList.toggle("hidden", !parts.length);
    $("#rescore-text").textContent = parts.join(" ") + (parts.length ? " Rescore scores them locally; GitHub labels change only after you apply them." : "");
    $("#labels-banner").classList.toggle("hidden", !d.pending_labels);
    $("#labels-text").textContent = `${d.pending_labels} PRs have a new score that isn't on GitHub yet. Labels you set by hand are left alone.`;
  }
  function renderReport(rep) {
    const box = $("#rescore-report");
    const movers = rep.biggest_movers.map(c => `<tr><td class="num">${c.old_llm} → ${c.new_llm}</td><td>${esc(c.key)}</td><td>${esc(c.title)}</td></tr>`).join("");
    const months = rep.monthly.filter(m => m.before !== m.after).map(m => `<tr><td>${m.month}</td><td class="num">${m.before}</td><td class="num">${m.after}</td></tr>`).join("");
    box.innerHTML = `<h2>Rescore result</h2><p class="sub">Scored ${rep.scored} PRs. ${rep.changed} changed (${rep.up} up, ${rep.down} down, average ${rep.avg_delta >= 0 ? "+" : ""}${rep.avg_delta}).${rep.errors.length ? ` <span class="error">${rep.errors.length} failed.</span>` : ""}</p>
      ${movers ? `<h2 style="margin-top:12px">Biggest changes</h2><table><tbody>${movers}</tbody></table>` : ""}
      ${months ? `<h2 style="margin-top:12px">Monthly complexity</h2><table><thead><tr><th>Month</th><th>Before</th><th>After</th></tr></thead><tbody>${months}</tbody></table>` : ""}`;
    box.classList.remove("hidden");
  }
  function syncSummary(r) {
    const sy = r.sync, sc = r.score;
    let t = `Synced ${sy.found} merged PRs: ${sy.new} new${sy.failed.length ? `, ${sy.failed.length} failed to download (retried next time)` : ""}.`;
    t += sc ? ` Scored ${sc.scored} PRs${sc.errors.length ? `, ${sc.errors.length} failed` : ""}.` : " Nothing new to score.";
    return t;
  }
  async function poll() {
    const s = await api("/api/rescore/status");
    const running = s.state === "running";
    const syncing = running && s.kind === "sync-score";
    $("#rescore-btn").disabled = running; $("#labels-btn").disabled = running; $("#sync-btn").disabled = running;
    $("#rescore-progress").classList.toggle("hidden", !(running && s.kind === "rescore"));
    $("#sync-progress").classList.toggle("hidden", !(syncing && s.total));
    if (syncing) {
      $("#sync-banner").classList.remove("hidden");
      if (s.total) { $("#sync-progress").max = s.total; $("#sync-progress").value = s.done; }
      const counts = s.total ? ` ${s.done}/${s.total}` : "";
      $("#sync-text").textContent = s.phase === "score" ? `Scoring new PRs…${counts}` : `Syncing from GitHub…${counts}`;
    }
    if (running) {
      if (s.kind === "rescore" && s.total) { $("#rescore-progress").max = s.total; $("#rescore-progress").value = s.done; }
      if (s.kind !== "sync-score") $("#rescore-text").textContent = s.kind === "rescore" ? `Rescoring… ${s.done}/${s.total || "?"}` : "Writing labels to GitHub…";
      setTimeout(poll, 1500);
      return;
    }
    if (s.state === "done" || s.state === "error") {
      await load();
      if (s.kind === "sync-score") {
        $("#sync-banner").classList.remove("hidden");
        $("#sync-text").textContent = s.state === "error" ? `Sync & score failed: ${s.error}` : syncSummary(s.result);
      } else if (s.state === "error") alert(`Job failed: ${s.error}`);
      else if (s.kind === "rescore" && !state.shownReport) { renderReport(s.result); state.shownReport = true; }
    }
  }
  $("#sync-btn").onclick = async () => {
    try { await api("/api/sync-score/start", { method: "POST" }); poll(); } catch (e) { alert(e.message); }
  };
  $("#rescore-btn").onclick = async () => {
    try { state.shownReport = false; await api("/api/rescore/start", { method: "POST" }); poll(); } catch (e) { alert(e.message); }
  };
  $("#labels-btn").onclick = async () => {
    if (!confirm(`Write complexity labels to ${state.data.pending_labels} PRs on GitHub?`)) return;
    try { await api("/api/labels/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); poll(); } catch (e) { alert(e.message); }
  };

  // ---- Wiring ------------------------------------------------------------
  document.querySelectorAll("#f-gran button").forEach(b => b.onclick = () => {
    state.gran = b.dataset.v;
    document.querySelectorAll("#f-gran button").forEach(x => x.classList.toggle("on", x === b));
    render();
  });
  ["#f-from", "#f-to", "#f-repo"].forEach(s => $(s).onchange = render);
  document.querySelectorAll("#pr-table th[data-k]").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    state.sort = { k, dir: state.sort.k === k ? -state.sort.dir : -1 };
    renderTable(filtered());
  });
  let resizeTimer;
  addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => state.data && render(), 150); });

  async function load() {
    state.data = STATIC || await api("/api/data");
    const repos = [...new Set(state.data.prs.map(p => p.repo))].sort();
    const sel = $("#f-repo"), cur = sel.value;
    sel.innerHTML = `<option value="">All repositories</option>` + repos.map(r => `<option ${r === cur ? "selected" : ""}>${esc(r)}</option>`).join("");
    const scored = state.data.prs.filter(p => p.score != null).length;
    $("#meta").textContent = `${scored} scored PRs · rubric v${state.data.rubric_version}${STATIC ? ` · exported ${STATIC.exported_at}` : ""}`;
    renderBanners();
    document.getElementById("empty-note")?.remove();
    if (!scored) {
      document.querySelector("main").insertAdjacentHTML("afterbegin", `<div class="card" id="empty-note" style="margin-bottom:16px">No scored PRs yet. Click <b>Sync &amp; score</b> above, or run <code>python -m velocity sync</code> then <code>python -m velocity score</code>.</div>`);
    }
    render();
  }
  load().then(() => { if (!STATIC) poll(); }).catch(e => { document.querySelector("main").innerHTML = `<p class="error">Failed to load data: ${esc(e.message)}</p>`; });
})();
