/* 主面板：连接 /api/stream，维护历史，每次采样只改文字和两张小图。
   省资源要点：没有常驻动画；页面隐藏时不画；颜色变量只在换肤时重新读取。 */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const store = {
    get(k, d) { try { const v = localStorage.getItem("ps." + k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem("ps." + k, JSON.stringify(v)); } catch (_) { } },
  };
  $$(".spark-path").forEach(e => e.setAttribute("d", F.sparkPath()));

  /* ================= 状态 ================= */
  const S = {
    info: {}, snap: null, interval: 1, maxLen: 600, cfg: {},
    h: { t: [], cpu: [], mem: [], gpu: [], disk: [], rx: [], tx: [] },
    hover: null, sort: store.get("sort", "cpu"),
    visible: store.get("series", { cpu: true, mem: true, gpu: true, disk: false }),
    findingAt: 0, findingKey: "",
  };
  const SERIES = [["cpu", "处理器", "--accent"], ["mem", "内存", "--chart2"], ["gpu", "显卡", "--chart3"], ["disk", "磁盘", "--faint"]];

  function pushHist(r) {
    const h = S.h;
    h.t.push(r.t); h.cpu.push(r.cpu); h.mem.push(r.mem); h.gpu.push(r.gpu); h.disk.push(r.disk); h.rx.push(r.rx); h.tx.push(r.tx);
    if (h.t.length > S.maxLen) for (const k in h) h[k].splice(0, h[k].length - S.maxLen);
  }

  /* ================= 工具 ================= */
  const B = (n, d) => F.bytes(n, d);
  const unit = (pair) => `${pair[0]}<span class="u">${pair[1]}</span>`;
  const setText = (el, t) => { if (el.textContent !== t) el.textContent = t; };
  const setHTML = (el, t) => { if (el.innerHTML !== t) el.innerHTML = t; };
  function meter(el, ratio, cls) {
    const i = el.firstElementChild;
    i.style.transform = `scaleX(${Math.max(0, Math.min(1, ratio || 0)).toFixed(3)})`;
    el.classList.toggle("warn", cls === "warn"); el.classList.toggle("bad", cls === "bad");
  }
  function kpi(id, value, hotAt, sub) {
    const el = $("#" + id), ok = value != null && !isNaN(value);
    setText($(".k-value b", el), ok ? String(Math.round(value)) : "–");
    meter($(".meter", el), ok ? value / 100 : 0);
    el.classList.toggle("hot", ok && value >= hotAt);
    el.classList.toggle("na", !ok);
    setText($(".k-sub", el), sub || "");
  }
  function cpuTemp(s) {
    const t = (s.sensors || []).filter(x => x.group === "cpu" && x.kind === "temp");
    if (!t.length) return null;
    const p = t.find(x => /package|tctl|tdie/i.test(x.name));
    return p ? p.value : Math.max(...t.map(x => x.value));
  }
  const short = (name) => String(name || "").replace(/\.exe$/i, "");

  /* ================= 一句话结论（标题下面） ================= */
  function finding(s) {
    const voice = (S.cfg.maid || {}).enabled !== false;
    const g = s.gpu && s.gpu[0], top = (s.procs.cpu || [])[0], b = (x) => `<b>${F.esc(x)}</b>`;
    const who = top && top.cpu >= 1 ? `${b(short(top.name))}（${top.cpu.toFixed(1)}%）` : null;
    const hotTemp = (s.sensors || []).filter(x => x.kind === "temp" && x.value >= 85).sort((a, c) => c.value - a.value)[0];
    const fullVol = (s.disk.volumes || []).filter(v => !v.removable && v.percent >= 95)[0];
    let key, text;
    if (s.cpu.percent >= 85) {
      key = "cpu"; text = voice ? `CPU 正在全力奔跑（${b(Math.round(s.cpu.percent) + "%")}）${who ? "，最忙的是 " + who : ""}～`
        : `处理器 ${b(Math.round(s.cpu.percent) + "%")}${who ? "，最忙的是 " + who : ""}。`;
    } else if (hotTemp) {
      key = "temp"; text = voice ? `${b(hotTemp.name)} 有点热（${b(Math.round(hotTemp.value) + " ℃")}），留意一下散热哦。`
        : `${b(hotTemp.name)} ${b(Math.round(hotTemp.value) + " ℃")}，温度偏高。`;
    } else if (s.mem.percent >= 88) {
      const tm = (s.procs.mem || [])[0];
      key = "mem"; text = voice ? `内存快满了（${b(Math.round(s.mem.percent) + "%")}）${tm ? "，占得最多的是 " + b(short(tm.name)) + "（" + B(tm.mem).join(" ") + "）" : ""}。`
        : `内存 ${b(Math.round(s.mem.percent) + "%")}${tm ? "，占用最多：" + b(short(tm.name)) + " " + B(tm.mem).join(" ") : ""}。`;
    } else if (g && g.util >= 90) {
      key = "gpu"; text = voice ? `显卡火力全开（${b(Math.round(g.util) + "%")}）${g.temp != null ? "，" + Math.round(g.temp) + " ℃" : ""}，我帮你盯着～`
        : `显卡 ${b(Math.round(g.util) + "%")}${g.temp != null ? "，" + Math.round(g.temp) + " ℃" : ""}。`;
    } else if (fullVol) {
      const m = fullVol.mount.replace(/\\$/, "");
      key = "disk"; text = voice ? `${b(m)} 只剩 ${b(B(fullVol.total - fullVol.used).join(" "))} 了，记得清理一下～`
        : `${b(m)} 剩余 ${b(B(fullVol.total - fullVol.used).join(" "))}。`;
    } else {
      key = "calm"; text = voice ? `一切平稳，我帮你看着呢。${who ? "现在最忙的是 " + who + "。" : ""}`
        : `一切平稳。${who ? "最忙的是 " + who + "。" : ""}`;
    }
    const now = Date.now(), el = $("#finding");
    // 同一类结论 5 秒才刷新一次数字，读起来不跳；类别变了立刻换
    if (key !== S.findingKey || now - S.findingAt > 5000) {
      S.findingKey = key; S.findingAt = now;
      setHTML(el, text);
      el.classList.toggle("alert", key !== "calm");
    }
  }

  /* ================= 渲染 ================= */
  function render(s) {
    const info = S.info, g = s.gpu && s.gpu[0], t = cpuTemp(s);
    finding(s);
    kpi("k-cpu", s.cpu.percent, 85,
      [s.cpu.freq ? (s.cpu.freq / 1000).toFixed(2) + " GHz" : null, t != null ? Math.round(t) + " ℃" : `${info.cores || "?"} 核 ${info.threads || "?"} 线程`].filter(Boolean).join(" · "));
    kpi("k-mem", s.mem.percent, 88, `${B(s.mem.used)[0]} / ${B(s.mem.total, 1).join(" ")}`);
    kpi("k-gpu", g ? g.util : null, 90,
      g ? [g.temp != null ? Math.round(g.temp) + " ℃" : null, g.mem_total ? `显存 ${B(g.mem_used)[0]} / ${B(g.mem_total, 0).join(" ")}` : null].filter(Boolean).join(" · ") || g.name : "未检测到显卡");
    kpi("k-disk", s.disk.busy, 90, `读 ${F.rate(s.disk.read).join(" ")} · 写 ${F.rate(s.disk.write).join(" ")}`);

    // 网络
    setHTML($("#net-rx"), unit(F.rate(s.net.rx)));
    setHTML($("#net-tx"), unit(F.rate(s.net.tx)));
    setHTML($("#nics"), (s.net.nics || []).filter(n => !n.virtual).slice(0, 3).map(n =>
      `<li><span class="n">${F.esc(n.name)}</span><span class="v faint">↓ ${F.rate(n.rx).join(" ")}　↑ ${F.rate(n.tx).join(" ")}</span></li>`).join("")
      || `<li><span class="n faint">没有活动的网卡</span></li>`);

    renderProcs(s);

    // 磁盘空间
    const vols = s.disk.volumes || [];
    const free = vols.reduce((a, v) => a + (v.total - v.used), 0);
    setText($("#vol-free"), vols.length ? `共可用 ${B(free, 0).join(" ")}` : "");
    setHTML($("#vol-list"), vols.map(v => {
      const cls = v.percent >= 95 ? "bad" : v.percent >= 85 ? "warn" : "";
      return `<li class="vol"><div class="line"><span class="n">${F.esc(v.mount.replace(/\\$/, ""))}<small>${F.esc(v.fstype || "")}${v.removable ? " · 可移动" : ""}</small></span>
        <span class="v ${cls}">${B(v.total - v.used).join(" ")} <span class="faint">可用 / ${B(v.total, 0).join(" ")}</span></span></div>
        <div class="meter ${cls}"><i style="transform:scaleX(${(v.percent / 100).toFixed(3)})"></i></div></li>`;
    }).join("") || `<li><span class="n faint">—</span></li>`);

    // 温度与风扇
    const order = { cpu: 0, gpu: 1, disk: 2, board: 3 }, kord = { temp: 0, fan: 1, power: 2 };
    const sens = (s.sensors || []).slice().sort((a, c) => ((order[a.group] ?? 4) * 10 + (kord[a.kind] ?? 3)) - ((order[c.group] ?? 4) * 10 + (kord[c.kind] ?? 3))).slice(0, 7);
    const GN = { cpu: "处理器", gpu: "显卡", disk: "硬盘", board: "主板", other: "其他" };
    $("#sensor-empty").hidden = sens.length > 0;
    setHTML($("#sensor-list"), sens.map(x => {
      const cls = x.kind === "temp" ? (x.value >= 95 ? "bad" : x.value >= 85 ? "warn" : "") : "";
      const val = x.kind === "fan" && x.unit === "RPM" ? Math.round(x.value) : x.value.toFixed(x.value >= 100 ? 0 : 1);
      return `<li><span class="g">${GN[x.group] || "其他"}</span><span class="n" title="${F.esc(x.hw || "")}">${F.esc(x.name)}</span><span class="v ${cls}">${val} <span class="faint">${F.esc(x.unit)}</span></span></li>`;
    }).join(""));

    // 每个线程
    const pc = s.cpu.per_core || [], bars = $("#core-bars");
    if (bars.children.length !== pc.length) {
      bars.innerHTML = pc.map((_, i) => `<div class="b"><div class="col"><i></i></div>${pc.length <= 32 ? `<span class="lbl">${i}</span>` : ""}</div>`).join("");
    }
    pc.forEach((v, i) => {
      const col = bars.children[i].firstElementChild;
      col.firstElementChild.style.transform = `scaleY(${(Math.max(1, v) / 100).toFixed(3)})`;
      col.classList.toggle("hi", v >= 80);
    });
    setText($("#cores-sum"), pc.length ? `${pc.length} 个线程，${pc.filter(v => v >= 50).length} 个超过一半，最高 ${Math.round(Math.max(...pc))}%` : "");

    // 顶栏：这台电脑是什么 + 本程序自己的占用（省资源是 PerfSkin 的承诺，所以摆在明面上）
    const bat = s.battery, me = s.self;
    setText($("#scope"), [info.os, info.cpu_name && info.cpu_name.replace(/\(R\)|\(TM\)|CPU|Processor|\d+-Core/gi, "").replace(/\s+/g, " ").trim(),
      g && g.name.replace(/NVIDIA |GeForce |\(演示\)/g, ""), B(info.mem_total, 0).join(" ") + " 内存",
      bat ? `电池 ${Math.round(bat.percent)}%${bat.plugged ? " 充电中" : ""}` : null, info.demo ? "演示数据" : null].filter(Boolean).join(" · "));
    setText($("#self"), me ? `自身 ${me.cpu.toFixed(1)}% · ${B(me.mem, 0).join(" ")}` : "");
    setText($("#uptime"), `已开机 ${F.dur(s.uptime)}`);
  }

  function renderProcs(s) {
    const list = (s.procs[S.sort] || []).slice(0, S.procRows);
    setText($("#proc-count"), s.procs.count ? `共 ${s.procs.count} 个进程` : "");
    if (!list.length) {
      setHTML($("#proc-body"), `<tr><td colspan="4" class="faint">${s.procs.paused ? "正在统计…" : "—"}</td></tr>`);
      return;
    }
    const maxC = Math.max(1, ...list.map(p => p.cpu)), maxM = Math.max(1, ...list.map(p => p.mem));
    setHTML($("#proc-body"), list.map((p, i) => {
      const lead = S.sort === "cpu" ? i === 0 : false, leadM = S.sort === "mem" ? i === 0 : false;
      return `<tr><td class="name" title="${F.esc(p.name)}">${F.esc(short(p.name))}<small>${p.count > 1 ? "×" + p.count : ""}</small></td>
        <td class="num"><div class="cell">${p.cpu.toFixed(1)}%<div class="meter${lead ? " lead" : ""}"><i style="transform:scaleX(${(p.cpu / maxC).toFixed(3)})"></i></div></div></td>
        <td class="num"><div class="cell">${B(p.mem).join(" ")}<div class="meter${leadM ? " lead" : ""}"><i style="transform:scaleX(${(p.mem / maxM).toFixed(3)})"></i></div></div></td>
        <td class="act">${p.protected ? `<span class="faint" title="系统关键进程">—</span>` : `<button class="end" data-name="${F.esc(p.name)}" data-count="${p.count}">结束</button>`}</td></tr>`;
    }).join(""));
  }

  /* 进程表的行数跟右边一列（磁盘 + 温度）对齐，两列底边齐平，不留空洞 */
  S.procRows = 8;
  function fitProcRows() {
    if (innerWidth <= 1100) { S.procRows = 8; return; }
    const stack = $(".stack"), card = $("#procs"), body = $("#proc-body");
    if (stack.offsetHeight === S.stackH && S.procRows === S.fitRows) return;   // 右列高度没变就不用重算
    S.stackH = stack.offsetHeight;
    const row = 42, used = card.offsetHeight - body.offsetHeight;
    const n = Math.max(6, Math.min(15, Math.floor((stack.offsetHeight - used) / row)));
    S.fitRows = n;
    if (n !== S.procRows) { S.procRows = n; if (S.snap) renderProcs(S.snap); }
  }

  /* ================= 图表 ================= */
  let layout = null;
  function drawCharts() {
    if (document.hidden || !S.h.t.length) return;
    layout = Charts.timeline($("#tl-canvas"), S.h.t,
      SERIES.map(([k, , c]) => ({ key: k, color: Charts.color(c), values: S.h[k], visible: S.visible[k], fill: k === "cpu" })),
      { hover: S.hover, span: S.maxLen * S.interval });
    const max = Charts.mirror($("#net-canvas"), S.h.rx, S.h.tx, Math.max(30, Math.round(90 / S.interval)));
    setText($("#net-scale"), `量程 ${F.rate(max).join(" ")}`);
  }

  // 图例 = 开关
  $("#legend").innerHTML = SERIES.map(([k, l, c]) =>
    `<button data-k="${k}" aria-pressed="${!!S.visible[k]}"><i class="dot" style="--c:var(${c})"></i>${l}</button>`).join("");
  $("#legend").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    S.visible[b.dataset.k] = !S.visible[b.dataset.k];
    b.setAttribute("aria-pressed", S.visible[b.dataset.k]); store.set("series", S.visible); drawCharts();
  });
  const cv = $("#tl-canvas"), tip = $("#tl-tip");
  cv.addEventListener("mousemove", e => {
    if (!layout) return;
    const r = cv.getBoundingClientRect(), x = e.clientX - r.left;
    const t = layout.t0 + (x - layout.L) / layout.pw * (layout.t1 - layout.t0);
    let lo = 0, hi = S.h.t.length - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (S.h.t[m] < t) lo = m + 1; else hi = m; }
    S.hover = lo; drawCharts();
    tip.hidden = false;
    tip.innerHTML = `<div class="t num">${F.clock(S.h.t[lo])}</div>` + SERIES.filter(([k]) => S.visible[k] && S.h[k][lo] != null)
      .map(([k, l, c]) => `<div class="num"><i class="dot" style="--c:var(${c})"></i> ${l} ${(+S.h[k][lo]).toFixed(1)}%</div>`).join("");
    tip.style.left = Math.min(r.width - tip.offsetWidth - 4, layout.X(S.h.t[lo]) + 12) + "px";
  });
  cv.addEventListener("mouseleave", () => { S.hover = null; tip.hidden = true; drawCharts(); });
  addEventListener("resize", drawCharts);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && S.snap) { render(S.snap); drawCharts(); } });

  // 进程排序
  $$("#procs .seg button").forEach(b => {
    b.setAttribute("aria-selected", b.dataset.sort === S.sort);
    b.addEventListener("click", () => {
      S.sort = b.dataset.sort; store.set("sort", S.sort);
      $$("#procs .seg button").forEach(x => x.setAttribute("aria-selected", x === b));
      if (S.snap) renderProcs(S.snap);
    });
  });

  /* ================= 请求 ================= */
  async function post(url, body, raw) {
    const r = await fetch(url, {
      method: "POST", body: raw ? body : JSON.stringify(body || {}),
      headers: Object.assign({ "X-Token": window.PM_TOKEN }, raw ? raw : { "Content-Type": "application/json" }),
    });
    let d = {};
    try { d = await r.json(); } catch (_) { }
    if (!r.ok || d.ok === false) throw new Error(d.error || `请求失败（${r.status}）`);
    return d;
  }
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-action]"); if (!b) return;
    post("/api/action/" + b.dataset.action).catch(() => { });
  });

  // 结束进程（先确认）
  const cf = $("#confirm");
  let pending = null;
  $("#proc-body").addEventListener("click", e => {
    const b = e.target.closest(".end"); if (!b) return;
    pending = b.dataset.name;
    $("#cf-name").textContent = short(pending);
    const n = +b.dataset.count;
    $("#cf-body").textContent = `将结束${n > 1 ? "全部 " + n + " 个" : "这个"}「${pending}」进程，里面没保存的内容会丢失。系统关键进程不会被结束。`;
    cf.showModal();
  });
  $("#cf-cancel").addEventListener("click", () => cf.close());
  $("#cf-ok").addEventListener("click", async () => {
    const name = pending; cf.close();
    try {
      const r = await post("/api/process/end", { name });
      flash(r.denied ? `已结束 ${r.ended} 个，${r.denied} 个没有权限` : `已结束 ${short(name)}`);
    } catch (err) { flash(err.message, true); }
  });
  function flash(msg, bad) {
    const el = $("#finding");
    el.innerHTML = `<b>${F.esc(msg)}</b>`; el.classList.toggle("alert", !!bad);
    S.findingAt = Date.now() + 3000;                // 让提示停留几秒
  }

  /* ================= 皮肤 ================= */
  // 立绘列的宽度 = 第一屏卡片高度 × 图片宽高比（图片自己决定占多宽，数字区自动让位）
  function fitFigure() {
    const img = $("#skin-img"), hero = $("#hero");
    if (!img.naturalWidth) return;
    const w = Math.round(hero.offsetHeight * img.naturalWidth / img.naturalHeight);
    hero.style.setProperty("--fig-w", Math.min(w, hero.offsetWidth * .5) + "px");
  }
  addEventListener("resize", fitFigure);
  let skinVer = 0;
  function applySkin(skin) {
    if (!skin) return;
    document.documentElement.dataset.layout = skin.image ? skin.layout : "none";
    const img = $("#skin-img");
    if (skin.image && img.getAttribute("src") !== skin.image) {
      img.onload = fitFigure; img.src = skin.image;
    } else if (!skin.image) img.removeAttribute("src");
    fitFigure();
    const link = $("#skin-css"), next = link.cloneNode();
    next.href = "/skin.css?v=" + (++skinVer) + "-" + Date.now();
    next.onload = () => { link.remove(); next.id = "skin-css"; Charts.invalidate(); drawCharts(); };
    link.after(next);
    S.skin = skin;
    window.dispatchEvent(new CustomEvent("ps:skin", { detail: skin }));
  }

  /* ================= 连接 ================= */
  const live = $("#live");
  function setLive(on) {
    live.classList.toggle("on", on); live.classList.toggle("off", !on);
    $("span", live).textContent = on ? `实时 · 每 ${S.interval} 秒` : "已断开，正在重连";
    document.body.classList.toggle("offline", !on);
  }
  function tick(s, fromHello) {
    S.snap = s;
    if (!fromHello && s.h) pushHist(s.h);
    if (document.hidden) return;                    // 隐藏时只记历史，不画
    render(s); drawCharts(); fitProcRows();
  }
  function connect() {
    const es = new EventSource("/api/stream");
    es.addEventListener("hello", e => {
      const d = JSON.parse(e.data);
      S.info = d.info; S.interval = d.info.interval; S.maxLen = d.info.history_len;
      for (const k in S.h) S.h[k] = [];
      const f = d.history.fields, ix = Object.fromEntries(f.map((k, i) => [k, i]));
      d.history.rows.forEach(r => pushHist({ t: r[ix.t], cpu: r[ix.cpu], mem: r[ix.mem], gpu: r[ix.gpu], disk: r[ix.disk], rx: r[ix.rx], tx: r[ix.tx] }));
      setText($("#title"), d.info.host || "此电脑");
      setText($("#tl-range"), `近 ${Math.round(S.maxLen * S.interval / 60)} 分钟`);
      $$("[data-feature]").forEach(b => { b.hidden = !(d.info.features || []).includes(b.dataset.feature); });
      setLive(true);
      if (d.skin) applySkin(d.skin);
      if (d.snapshot) tick(d.snapshot, true);
    });
    es.addEventListener("tick", e => tick(JSON.parse(e.data)));
    es.addEventListener("skin", e => applySkin(JSON.parse(e.data)));
    es.onerror = () => setLive(false);
    es.onopen = () => setLive(true);
  }
  fetch("/api/config").then(r => r.json()).then(c => { S.cfg = c; }).catch(() => { });
  window.PS = { post, applySkin, state: S, setCfg: (c) => { S.cfg = c; S.findingAt = 0; if (S.snap) finding(S.snap); }, flash };
  connect();
})();
