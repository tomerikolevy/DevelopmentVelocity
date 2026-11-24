(() => {
  const $ = s => document.querySelector(s);
  const esc = Charts.esc;
  const st = { pair: null, types: {}, proposal: null, skipped: [] };

  async function api(path, body) {
    const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const r = await fetch(path, opts);
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }
  const busy = (msgEl, text) => { msgEl.innerHTML = text ? `<span class="spinner"></span> ${esc(text)}` : ""; };

  // ---- Pair ------------------------------------------------------------------
  const DIMS = [["scope", "Scope"], ["logic_depth", "Logic depth"], ["novelty", "Novelty"], ["risk", "Risk"]];
  const meter = v => `<span class="meter">${[1, 2, 3, 4, 5].map(i => `<i class="${i <= v ? "on" : ""}"></i>`).join("")}</span>${v}/5`;
  function card(pr, which) {
    const c = pr.by_category || {};
    const cat = k => c[k] ? `${c[k].files} files, +${c[k].additions} / −${c[k].deletions}` : "—";
    const ct = pr.change_types || {};
    return `<div class="card pr-card">
      <div class="which">PR ${which}</div>
      <h3><a href="${esc(pr.url)}" target="_blank" rel="noopener">${esc(pr.title)}</a></h3>
      <div class="meta">${esc(pr.repo)} · ${(pr.merged_at || "").slice(0, 10)} · confidence ${esc(pr.confidence || "?")}</div>
      <p class="summary">${esc(pr.summary || "No summary.")}</p>
      <dl>
        ${DIMS.map(([k, l]) => `<dt>${l}</dt><dd>${pr.dimensions ? meter(pr.dimensions[k]) : "—"}</dd>`).join("")}
        <dt>Source</dt><dd>${cat("source")}</dd>
        <dt>Tests</dt><dd>${cat("tests")}</dd>
        <dt>Config / docs</dt><dd>${cat("config")} · ${cat("docs")}</dd>
        <dt>Files</dt><dd>${ct.new || 0} new, ${ct.modified || 0} modified, ${ct.deleted || 0} deleted, ${ct.renamed || 0} renamed · ${pr.directories_touched ?? "?"} dirs</dd>
        <dt>Languages</dt><dd>${esc((pr.languages || []).join(", ") || "—")}${pr.excluded_count ? ` · ${pr.excluded_count} generated/lock files ignored` : ""}</dd>
      </dl>
      <div class="rationale"><strong>Why ${pr.score}:</strong> ${esc(pr.rationale || "")}</div>
    </div>`;
  }
  async function loadPair() {
    resetDispute();
    $("#verdict-msg").textContent = "";
    const j = await api("/api/calibrate/pair");
    st.pair = j.pair;
    // A skipped pair can come back at random; try a few times to avoid it.
    for (let n = 0; st.pair && n < 5 && st.skipped.includes(st.pair.a.key + "|" + st.pair.b.key); n++) st.pair = (await api("/api/calibrate/pair")).pair;
    const has = !!st.pair;
    $("#verdict-actions").classList.toggle("hidden", !has);
    if (!has) {
      $("#pair-title").textContent = "No pairs left to review";
      $("#pair-sub").textContent = `Every pair of same-score PRs scored with rubric v${j.rubric_version} has been reviewed, or too few PRs share a score.`;
      $("#pair").innerHTML = "";
      return;
    }
    $("#pair-title").textContent = `Both scored ${st.pair.score}`;
    $("#pair-sub").textContent = "Do these two PRs really have the same complexity?";
    $("#pair").innerHTML = card(st.pair.a, "A") + card(st.pair.b, "B");
  }

  // ---- Verdicts --------------------------------------------------------------
  function resetDispute() {
    $("#dispute-form").classList.add("hidden");
    $("#explanation").value = ""; $("#us-a").value = ""; $("#us-b").value = "";
    document.querySelectorAll("#dispute-types input").forEach(i => i.checked = false);
    $("#dispute-msg").textContent = "";
  }
  $("#agree-btn").onclick = async () => {
    try {
      await api("/api/calibrate/review", { a: st.pair.a.key, b: st.pair.b.key, score: st.pair.score, verdict: "agree" });
      $("#verdict-msg").textContent = "Recorded.";
      await Promise.all([loadPair(), loadState()]);
    } catch (e) { alert(e.message); }
  };
  $("#dispute-btn").onclick = () => { $("#dispute-form").classList.remove("hidden"); $("#explanation").focus(); };
  $("#cancel-dispute").onclick = resetDispute;
  $("#skip-btn").onclick = () => { st.skipped.push(st.pair.a.key + "|" + st.pair.b.key); loadPair(); };

  async function submitDispute(analyze) {
    const type = document.querySelector("#dispute-types input:checked")?.value;
    const explanation = $("#explanation").value.trim();
    if (!type) return alert("Pick what's wrong with the scores.");
    if (!explanation) return alert("Please explain why — the rubric change is based on it.");
    const us = {};
    if ($("#us-a").value) us.a = +$("#us-a").value;
    if ($("#us-b").value) us.b = +$("#us-b").value;
    const btns = [$("#submit-analyze"), $("#submit-later")];
    btns.forEach(b => b.disabled = true);
    busy($("#dispute-msg"), analyze ? "Analyzing your feedback against the rubric… (can take a minute)" : "Saving…");
    try {
      const j = await api("/api/calibrate/review", {
        a: st.pair.a.key, b: st.pair.b.key, score: st.pair.score, verdict: "dispute",
        dispute_type: type, explanation, user_scores: us, analyze,
      });
      if (j.proposal) showProposal(j.proposal);
      await Promise.all([loadPair(), loadState()]);
    } catch (e) {
      busy($("#dispute-msg"));
      alert(`Failed: ${e.message}`);
      await loadState();
    } finally { btns.forEach(b => b.disabled = false); }
  }
  $("#submit-analyze").onclick = () => submitDispute(true);
  $("#submit-later").onclick = () => submitDispute(false);

  // ---- Proposal --------------------------------------------------------------
  function diffHtml(diff) {
    return diff.split("\n").map(l => {
      const cls = l.startsWith("+++") || l.startsWith("---") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "hunk" : "";
      return `<span class="${cls}">${esc(l)}</span>`;
    }).join("\n");
  }
  function showProposal(p) {
    st.proposal = p;
    const box = $("#proposal");
    box.innerHTML = `
      <h2>Proposed rubric change <span class="muted">v${p.base_version} → v${p.base_version + 1}</span></h2>
      <p class="sub">Based on ${p.review_ids.length} dispute${p.review_ids.length > 1 ? "s" : ""}. Nothing changes until you accept.</p>
      <h3>Diagnosis</h3><p style="margin:0">${esc(p.diagnosis)}</p>
      <h3>Changes</h3><ul>${p.changes.map(c => `<li>${esc(c)}</li>`).join("")}</ul>
      <h3>Predicted scores under the new rubric</h3><ul>${p.predicted_scores.map(s => `<li><strong>${s.score}</strong> — ${esc(s.key)}: ${esc(s.reason)}</li>`).join("")}</ul>
      <h3>Estimated impact</h3><p style="margin:0">About <strong>${p.affected_estimate}</strong> other PRs would likely change score${p.affected_examples.length ? `, e.g. ${p.affected_examples.slice(0, 6).map(esc).join(", ")}` : ""}.</p>
      <h3>Diff</h3><pre class="diff">${diffHtml(p.diff || "(no textual change)")}</pre>
      <details><summary style="cursor:pointer">Edit the new rubric before accepting</summary>
        <textarea id="edit-rubric" rows="18" style="margin-top:8px;font-family:ui-monospace,monospace;font-size:12px">${esc(p.new_rubric)}</textarea>
      </details>
      <label style="display:block;margin-top:12px">Note for the changelog (optional)<input id="accept-note" style="width:100%;margin-top:4px"></label>
      <div class="actions">
        <button class="primary" id="p-accept">Accept → rubric v${p.base_version + 1}</button>
        <button id="p-save">Save for later</button>
        <button class="danger" id="p-reject">Reject</button>
        <span class="muted" id="p-msg"></span>
      </div>`;
    box.classList.remove("hidden");
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    $("#p-accept").onclick = async () => {
      const edited = $("#edit-rubric").value;
      try {
        const j = await api(`/api/calibrate/proposal/${p.id}/accept`, { rubric: edited !== p.new_rubric ? edited : null, note: $("#accept-note").value });
        box.innerHTML = `<h2>Rubric v${j.rubric_version} is now active</h2><p>PRs scored with older rubrics are now outdated. <a href="/">Go to the dashboard</a> to rescore them and review the changes before labels are updated.</p>`;
        await Promise.all([loadState(), loadPair(), loadRubric()]);
      } catch (e) { alert(e.message); }
    };
    $("#p-save").onclick = async () => { await api(`/api/calibrate/proposal/${p.id}/save`, {}); box.classList.add("hidden"); loadState(); };
    $("#p-reject").onclick = async () => {
      if (!confirm("Reject this proposal? Its dispute goes back to the open list.")) return;
      await api(`/api/calibrate/proposal/${p.id}/reject`, {}); box.classList.add("hidden"); loadState();
    };
  }

  // ---- State: stats, open disputes, saved proposals -----------------------------
  async function loadState() {
    const j = await api("/api/calibrate/state");
    st.types = j.dispute_types;
    if (!$("#dispute-types").children.length) {
      $("#dispute-types").innerHTML = Object.entries(j.dispute_types).map(([k, v]) => `<label><input type="radio" name="dtype" value="${k}">${esc(v)}</label>`).join("");
    }
    $("#meta").textContent = `rubric v${j.rubric_version}`;
    const cur = j.stats.by_version.find(v => v.version === j.rubric_version);
    const pct = v => v == null ? "—" : Math.round(v * 100) + "%";
    const hist = j.stats.by_version.map(v => `v${v.version}: ${pct(v.agreement)} of ${v.agree + v.dispute}`).join(" · ");
    $("#stats").innerHTML = `<div class="stats-row">
      <div><div class="v">${pct(cur?.agreement)}</div><div class="l">Agreement with rubric v${j.rubric_version}</div></div>
      <div><div class="v">${cur ? cur.agree + cur.dispute : 0}</div><div class="l">Pairs reviewed (v${j.rubric_version})</div></div>
      <div><div class="v">${j.stats.open_disputes}</div><div class="l">Open disputes</div></div>
      <div style="flex:1;min-width:240px"><div class="l">Agreement by rubric version</div><div style="margin-top:4px">${hist || "No reviews yet."}</div>
        ${j.stats.by_score.length ? `<div class="l" style="margin-top:8px">Disputes by score: ${j.stats.by_score.filter(s => s.dispute).map(s => `${s.score} (${s.dispute})`).join(", ") || "none"}</div>` : ""}</div>
    </div>`;

    $("#disputes").innerHTML = j.open_disputes.length ? j.open_disputes.map(r => `
      <div class="list-item"><input type="checkbox" value="${r.id}" class="d-check">
        <div><div><strong>${r.score}</strong> · ${esc(st.types[r.dispute_type] || r.dispute_type)}${r.rubric_version !== j.rubric_version ? ` <span class="tag">rubric v${r.rubric_version}</span>` : ""}</div>
        <div class="muted">A: ${esc(r.a_title)}<br>B: ${esc(r.b_title)}</div>
        <div>“${esc(r.explanation)}”</div></div></div>`).join("") +
      `<div class="actions"><button class="primary" id="analyze-selected">Analyze selected</button><span class="muted" id="analyze-msg"></span></div>`
      : `<p class="muted">None.</p>`;
    $("#analyze-selected")?.addEventListener("click", async () => {
      const ids = [...document.querySelectorAll(".d-check:checked")].map(c => c.value);
      if (!ids.length) return alert("Select at least one dispute.");
      busy($("#analyze-msg"), "Analyzing…");
      try { showProposal((await api("/api/calibrate/analyze", { review_ids: ids })).proposal); await loadState(); }
      catch (e) { busy($("#analyze-msg")); alert(e.message); }
    });

    const saved = j.proposals.filter(p => p.status === "saved" || (p.status === "proposed" && p.id !== st.proposal?.id));
    $("#saved").innerHTML = saved.length ? saved.map(p => `
      <div class="list-item"><div style="flex:1"><div>${esc(p.changes[0] || p.diagnosis)}${p.changes.length > 1 ? ` <span class="muted">+${p.changes.length - 1} more</span>` : ""}</div>
      <div class="muted">based on rubric v${p.base_version} · ${p.created_at.slice(0, 10)}${p.base_version !== j.rubric_version ? " · outdated" : ""}</div></div>
      <button data-open="${p.id}">Open</button></div>`).join("") : `<p class="muted">None.</p>`;
    document.querySelectorAll("[data-open]").forEach(b => b.onclick = () => showProposal(j.proposals.find(p => p.id === b.dataset.open)));
  }

  async function loadRubric() {
    const j = await api("/api/rubric");
    $("#rubric-version").textContent = `v${j.version}`;
    $("#rubric-text").textContent = j.text;
  }
  $("#rubric-toggle").onclick = () => {
    const el = $("#rubric-text"); el.classList.toggle("hidden");
    $("#rubric-toggle").textContent = el.classList.contains("hidden") ? "Show rubric" : "Hide rubric";
  };

  Promise.all([loadState(), loadPair(), loadRubric()]).catch(e => { document.querySelector("main").innerHTML = `<p class="error">${esc(e.message)}</p>`; });
})();
