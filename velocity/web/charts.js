// Minimal SVG charts with hover tooltips. No dependencies, works offline.
const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const M = { top: 12, right: 12, bottom: 28, left: 40 };
  let tip;

  function el(tag, attrs = {}, parent) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (parent) parent.appendChild(n);
    return n;
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  function tooltip() {
    if (!tip) { tip = document.createElement("div"); tip.className = "tooltip hidden"; document.body.appendChild(tip); }
    return tip;
  }
  function showTip(evt, html) {
    const t = tooltip();
    t.innerHTML = html; t.classList.remove("hidden");
    const w = t.offsetWidth, h = t.offsetHeight;
    let x = evt.clientX + 14, y = evt.clientY + 14;
    if (x + w > innerWidth - 8) x = evt.clientX - w - 14;
    if (y + h > innerHeight - 8) y = evt.clientY - h - 14;
    t.style.left = x + "px"; t.style.top = y + "px";
  }
  function hideTip() { tooltip().classList.add("hidden"); }

  // Axis max = 4 equal steps of a "nice" size (1, 2, 2.5, 5 × 10^k), so tick labels stay round.
  function niceMax(v) {
    if (v <= 0) return 4;
    const raw = v / 4, p = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return Math.max(4 * m * p, 4) ;
    return 40 * p;
  }

  function frame(container, height, yMax, labels, yFmt = v => v) {
    container.innerHTML = "";
    const width = Math.max(container.clientWidth, 300);
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, height });
    container.appendChild(svg);
    const iw = width - M.left - M.right, ih = height - M.top - M.bottom;
    const g = el("g", { transform: `translate(${M.left},${M.top})` }, svg);
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const v = (yMax / ticks) * i, y = ih - (v / yMax) * ih;
      el("line", { x1: 0, x2: iw, y1: y, y2: y, stroke: "var(--grid)", "stroke-width": 1 }, g);
      const t = el("text", { x: -8, y: y + 4, "text-anchor": "end" }, g);
      t.textContent = yFmt(Math.round(v * 10) / 10);
    }
    const n = labels.length, step = iw / Math.max(n, 1);
    const every = Math.max(1, Math.ceil(n / Math.floor(iw / 64)));
    labels.forEach((l, i) => {
      if (i % every) return;
      const t = el("text", { x: step * i + step / 2, y: ih + 18, "text-anchor": "middle" }, g);
      t.textContent = l;
    });
    return { svg, g, iw, ih, step, y: v => ih - (v / yMax) * ih };
  }

  function legend(container, items) {
    const lg = document.createElement("div");
    lg.className = "legend";
    lg.innerHTML = items.map(i => `<span class="${i.line ? "line" : ""}" style="--c:${i.color}">${esc(i.name)}</span>`).join("");
    container.appendChild(lg);
  }

  // Bar path with 4px rounded top only, anchored square to the baseline.
  function barPath(x, y, w, h, r) {
    r = Math.min(r, w / 2, h);
    if (h <= 0) return "";
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  /** series: [{name, color, values}], optional line: {name, color, values}. */
  function stackedBars(container, { labels, series, line, height = 260, tipTitle = l => l }) {
    const totals = labels.map((_, i) => series.reduce((s, ser) => s + ser.values[i], 0));
    const yMax = niceMax(Math.max(...totals, ...(line ? line.values : [0]), 1));
    const f = frame(container, height, yMax, labels);
    const bw = Math.max(2, Math.min(36, f.step * 0.7)), gap = 2;
    labels.forEach((lab, i) => {
      let acc = 0;
      const x = f.step * i + (f.step - bw) / 2;
      const visible = series.filter(s => s.values[i] > 0);
      visible.forEach((s, j) => {
        const v = s.values[i];
        const y0 = f.y(acc), y1 = f.y(acc + v);
        const top = j === visible.length - 1;
        const h = Math.max(0, y0 - y1 - (top ? 0 : gap));
        if (top) el("path", { d: barPath(x, y1, bw, h, 4), fill: s.color }, f.g);
        else el("rect", { x, y: y1 + gap, width: bw, height: h, fill: s.color }, f.g);
        acc += v;
      });
      const hit = el("rect", { x: f.step * i, y: 0, width: f.step, height: f.ih, fill: "transparent" }, f.g);
      hit.addEventListener("mousemove", e => {
        const rows = series.filter(s => s.values[i] > 0).reverse()
          .map(s => `<div class="r"><span><i class="sw" style="background:${s.color}"></i>${esc(s.name)}</span><b>${s.values[i]}</b></div>`).join("");
        const extra = line ? `<div class="r"><span>${esc(line.name)}</span><b>${line.values[i].toFixed(1)}</b></div>` : "";
        showTip(e, `<div class="t">${esc(tipTitle(lab, i))}</div><div class="r"><span>Total</span><b>${totals[i]}</b></div>${rows}${extra}`);
      });
      hit.addEventListener("mouseleave", hideTip);
    });
    if (line) {
      const pts = line.values.map((v, i) => `${f.step * i + f.step / 2},${f.y(v)}`).join(" ");
      el("polyline", { points: pts, fill: "none", stroke: line.color, "stroke-width": 2, "stroke-linejoin": "round", "pointer-events": "none" }, f.g);
    }
    legend(container, [...series.map(s => ({ name: s.name, color: s.color })), ...(line ? [{ name: line.name, color: line.color, line: true }] : [])]);
  }

  function bars(container, { labels, values, color, height = 200, name = "Value", tipTitle = l => l, fmt = v => v }) {
    stackedBars(container, { labels, series: [{ name, color, values }], height, tipTitle });
    container.querySelector(".legend")?.remove();
  }

  function lineChart(container, { labels, values, color, height = 200, name = "Value", tipTitle = l => l, fmt = v => v.toFixed(1) }) {
    const defined = values.filter(v => v != null);
    const yMax = niceMax(Math.max(...defined, 1));
    const f = frame(container, height, yMax, labels);
    let d = "", pen = false;
    values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${f.step * i + f.step / 2},${f.y(v)}`; pen = true;
    });
    el("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }, f.g);
    const dot = el("circle", { r: 4, fill: color, stroke: "var(--surface-1)", "stroke-width": 2, class: "hidden" }, f.g);
    const cross = el("line", { y1: 0, y2: f.ih, stroke: "var(--text-muted)", "stroke-dasharray": "3 3", class: "hidden" }, f.g);
    labels.forEach((lab, i) => {
      const hit = el("rect", { x: f.step * i, y: 0, width: f.step, height: f.ih, fill: "transparent" }, f.g);
      hit.addEventListener("mousemove", e => {
        const v = values[i], cx = f.step * i + f.step / 2;
        cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.classList.remove("hidden");
        if (v != null) { dot.setAttribute("cx", cx); dot.setAttribute("cy", f.y(v)); dot.classList.remove("hidden"); }
        showTip(e, `<div class="t">${esc(tipTitle(lab, i))}</div><div class="r"><span>${esc(name)}</span><b>${v == null ? "—" : fmt(v)}</b></div>`);
      });
      hit.addEventListener("mouseleave", () => { hideTip(); dot.classList.add("hidden"); cross.classList.add("hidden"); });
    });
  }

  return { stackedBars, bars, lineChart, esc };
})();
