/* 迷你窗：只在可见时更新；没有动画。 */
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  const state = { interval: 1, snap: null, hist: [] };
  document.querySelectorAll(".spark-path").forEach(e => e.setAttribute("d", F.sparkPath()));

  function addHist(r) {
    if (!r || !Number.isFinite(r.t)) return;
    state.hist.push(r);
    while (state.hist.length && state.hist[0].t < r.t - 60) state.hist.shift();
  }
  function row(id, valueId, v, hotAt) {
    const ok = Number.isFinite(v), p = ok ? Math.max(0, Math.min(100, v)) : 0;
    $(valueId).textContent = ok ? Math.round(p) + "%" : "–";
    $(id + " .meter i").style.transform = `scaleX(${(p / 100).toFixed(3)})`;
    $(id).classList.toggle("hot", ok && p >= hotAt);
  }
  function cpuTemp(s) {
    const t = (s.sensors || []).filter(x => x.group === "cpu" && x.kind === "temp");
    if (!t.length) return null;
    const p = t.find(x => /package|tctl|tdie/i.test(x.name));
    return p ? p.value : Math.max(...t.map(x => x.value));
  }
  function draw(s) {
    if (document.hidden || !s) return;
    const g = s.gpu && s.gpu[0], t = cpuTemp(s);
    row("#cpu-row", "#cpu-value", s.cpu?.percent, 85);
    row("#mem-row", "#mem-value", s.mem?.percent, 88);
    row("#gpu-row", "#gpu-value", g ? g.util : NaN, 90);
    $("#cpu-temp").textContent = t == null ? "" : Math.round(t) + "℃";
    $("#gpu-temp").textContent = g && g.temp != null ? Math.round(g.temp) + "℃" : "";
    $("#mem-extra").textContent = s.mem ? F.bytes(s.mem.used, 1).join("") : "";
    const rx = s.net?.rx || 0, tx = s.net?.tx || 0;
    $("#net-rate").textContent = `↓${F.rate(rx).join("")}  ↑${F.rate(tx).join("")}`;
    const vals = state.hist.map(r => (r.rx || 0) + (r.tx || 0));
    Charts.spark($("#net-curve"), vals, Charts.color("--accent"), Charts.niceMax(Math.max(64 * 1024, ...vals) * 1.1), Math.max(2, Math.round(60 / state.interval)));
  }
  function setLive(on) {
    document.body.classList.toggle("offline", !on);
    $("#live span").textContent = on ? "实时" : "已断开";
  }
  function reloadSkin() {
    const link = $("#skin-css"), next = link.cloneNode();
    next.href = "/skin.css?v=" + Date.now();
    next.onload = () => { link.remove(); next.id = "skin-css"; Charts.invalidate(); draw(state.snap); };
    link.after(next);
  }
  const es = new EventSource("/api/stream");
  es.addEventListener("hello", e => {
    const d = JSON.parse(e.data);
    state.interval = d.info.interval || 1; state.hist = [];
    const ix = Object.fromEntries(d.history.fields.map((f, i) => [f, i]));
    d.history.rows.forEach(r => addHist({ t: r[ix.t], rx: r[ix.rx], tx: r[ix.tx] }));
    $("#host").textContent = d.info.host || "PerfSkin";
    setLive(true);
    if (d.snapshot) { state.snap = d.snapshot; draw(d.snapshot); }
  });
  es.addEventListener("tick", e => {
    const s = JSON.parse(e.data);
    if (s.h) addHist({ t: s.h.t, rx: s.h.rx, tx: s.h.tx });
    state.snap = s; draw(s);
  });
  es.addEventListener("skin", reloadSkin);
  es.onopen = () => setLive(true);
  es.onerror = () => setLive(false);
  document.addEventListener("visibilitychange", () => draw(state.snap));
  addEventListener("resize", () => draw(state.snap));
  document.addEventListener("dblclick", () => {
    fetch("/api/action/open-window", { method: "POST", headers: { "X-Token": window.PM_TOKEN, "Content-Type": "application/json" }, body: "{}" }).catch(() => { });
  });
})();
