/* 皮肤与设置抽屉。每个控件改完立即保存，右上角提示“已保存”。 */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const sheet = $("#sheet");
  let skins = [], active = null, settings = null;

  /* ---------------- 打开 / 关闭 / 切换页 ---------------- */
  function open(pane) {
    showPane(pane);
    if (!sheet.open) sheet.showModal();
    if (pane === "skins") loadSkins(); else loadSettings();
  }
  function showPane(pane) {
    $$(".sheet-head [data-pane]").forEach(b => b.setAttribute("aria-selected", b.dataset.pane === pane));
    $("#pane-skins").hidden = pane !== "skins";
    $("#pane-settings").hidden = pane !== "settings";
  }
  $("#open-skins").addEventListener("click", () => open("skins"));
  $("#open-settings").addEventListener("click", () => open("settings"));
  $("#sheet-close").addEventListener("click", () => sheet.close());
  sheet.addEventListener("click", e => { if (e.target === sheet) sheet.close(); });   // 点遮罩关闭
  $$(".sheet-head [data-pane]").forEach(b => b.addEventListener("click", () => open(b.dataset.pane)));

  let savedT = 0;
  function saved() {
    const el = $("#saved"); el.hidden = false;
    clearTimeout(savedT); savedT = setTimeout(() => { el.hidden = true; }, 1600);
  }
  function error(msg) {
    const el = $("#skin-err"); el.textContent = msg; el.hidden = !msg;
  }

  /* ---------------- 皮肤列表 ---------------- */
  async function loadSkins() {
    try {
      const d = await (await fetch("/api/skins")).json();
      skins = d.skins; active = d.active;
      renderSkins();
    } catch (_) { error("读取皮肤列表失败"); }
  }
  function renderSkins() {
    $("#skin-grid").innerHTML = skins.map(s => {
      const p = s.palette;
      const th = s.image
        ? `<span class="th" style="--sw-ground:${p.ground};background-image:url('${s.image}')"></span>`
        : `<span class="th noimg" style="background:${p.ground}"><i class="sw" style="background:${p.accent}"></i><i class="sw" style="background:${p.chart2}"></i><i class="sw" style="background:${p.chart3}"></i></span>`;
      return `<button class="skin" data-id="${F.esc(s.id)}" aria-pressed="${s.id === active}">${th}
        <span class="meta"><i class="sw" style="background:${p.accent}"></i><b>${F.esc(s.name)}</b></span></button>`;
    }).join("");
    renderEditor();
  }
  $("#skin-grid").addEventListener("click", async e => {
    const b = e.target.closest(".skin"); if (!b || b.dataset.id === active) return;
    try {
      await PS.post(`/api/skins/${encodeURIComponent(b.dataset.id)}/activate`);
      active = b.dataset.id; renderSkins(); saved();
    } catch (err) { error(err.message); }
  });

  /* ---------------- 上传 ---------------- */
  const drop = $("#drop"), file = $("#file");
  file.addEventListener("change", () => file.files[0] && upload(file.files[0]));
  ["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
  async function upload(f) {
    error("");
    if (!/^image\//.test(f.type)) return error("请选择图片文件（PNG / JPG / WebP）");
    if (f.size > 20 * 1024 * 1024) return error("图片太大了，上限 20 MB");
    drop.classList.add("busy"); $("b", drop).textContent = "正在取色…";
    try {
      const name = f.name.replace(/\.[^.]+$/, "").slice(0, 40);
      const d = await PS.post("/api/skins", f, { "Content-Type": f.type || "application/octet-stream", "X-Skin-Name": encodeURIComponent(name) });
      active = d.skin.id;
      await loadSkins(); saved();
    } catch (err) { error(err.message); }
    finally { drop.classList.remove("busy"); $("b", drop).textContent = "上传图片做皮肤"; file.value = ""; }
  }

  /* ---------------- 编辑当前皮肤 ---------------- */
  function current() { return skins.find(s => s.id === active); }
  function renderEditor() {
    const s = current(); if (!s) return;
    $("#se-name").value = s.name;
    $$("#se-layout button").forEach(b => {
      b.setAttribute("aria-checked", b.dataset.v === (s.image ? s.layout : "none"));
      b.disabled = !s.image && b.dataset.v !== "none";
    });
    $("#se-dim").value = Math.round((s.dim || .85) * 100);
    $("#se-dim-v").textContent = $("#se-dim").value + "%";
    $("#se-dim").disabled = !s.image;
    const colors = (s.colors || []).map(c => c.hex).slice(0, 7);
    const cur = (s.accent || "").toLowerCase();
    $("#se-swatches").innerHTML =
      `<button data-hex="" title="自动（图片里最合适的颜色）" aria-pressed="${!s.accent}" style="background:${colors[0] || s.palette.accent}"></button>` +
      colors.slice(1).map(h => `<button data-hex="${h}" title="${h}" aria-pressed="${cur === h}" style="background:${h}"></button>`).join("") +
      `<label title="自己挑一个颜色" aria-pressed="${!!s.accent && !colors.includes(cur)}">+<input type="color" id="se-color" value="${s.palette.accent}"></label>`;
    $("#se-delete").hidden = !!s.builtin;
  }
  async function update(changes) {
    const s = current(); if (!s) return;
    try {
      const d = await PS.post(`/api/skins/${encodeURIComponent(s.id)}/update`, changes);
      const i = skins.findIndex(x => x.id === s.id);
      skins[i] = d.skin; active = d.skin.id;
      renderSkins(); saved();
    } catch (err) { error(err.message); }
  }
  let nameT = 0;
  $("#se-name").addEventListener("input", e => { clearTimeout(nameT); nameT = setTimeout(() => update({ name: e.target.value }), 600); });
  $("#se-layout").addEventListener("click", e => { const b = e.target.closest("button"); if (b && !b.disabled) update({ layout: b.dataset.v }); });
  $("#se-dim").addEventListener("input", e => {
    $("#se-dim-v").textContent = e.target.value + "%";
    document.documentElement.style.setProperty("--skin-dim", e.target.value / 100);     // 拖动时先本地预览
  });
  $("#se-dim").addEventListener("change", e => { document.documentElement.style.removeProperty("--skin-dim"); update({ dim: e.target.value / 100 }); });
  $("#se-swatches").addEventListener("click", e => { const b = e.target.closest("button"); if (b) update({ accent: b.dataset.hex || null }); });
  $("#se-swatches").addEventListener("change", e => { if (e.target.id === "se-color") update({ accent: e.target.value }); });
  $("#se-delete").addEventListener("click", async () => {
    const s = current(); if (!s || s.builtin) return;
    if (!confirm(`删除皮肤「${s.name}」？图片会一起删掉，不能恢复。`)) return;
    try { await PS.post(`/api/skins/${encodeURIComponent(s.id)}/delete`); await loadSkins(); saved(); }
    catch (err) { error(err.message); }
  });

  /* ---------------- 设置 ---------------- */
  const LIMITS = [["cpu", "处理器超过", "%"], ["mem", "内存超过", "%"], ["gpu", "显卡超过", "%"], ["temp", "温度超过", "℃"],
    ["disk_free_gb", "磁盘剩余少于", "GB"], ["sustain_seconds", "持续多久才提醒", "秒"], ["cooldown_minutes", "同一项冷却", "分钟"]];
  async function loadSettings() {
    try { settings = await (await fetch("/api/settings")).json(); renderSettings(); } catch (_) { }
  }
  function renderSettings() {
    const s = settings;
    $("#st-interval").innerHTML = s.intervals.map(v => `<button role="radio" data-v="${v}" aria-checked="${v === s.interval}">${v} 秒</button>`).join("");
    $("#st-autostart").checked = s.autostart; $("#st-autostart").disabled = !s.autostart_supported;
    $("#st-open").checked = s.open_on_start;
    $("#st-bubble").checked = s.bubble;
    $("#st-topmost").checked = s.mini_topmost;
    $("#st-alerts").checked = !!s.alerts.enabled;
    $("#st-limits").innerHTML = LIMITS.map(([k, l, u]) =>
      `<label class="field"><span>${l}（${u}）</span><input type="number" data-k="${k}" value="${s.alerts[k]}" inputmode="decimal"></label>`).join("");
    $("#st-about").textContent = `PerfSkin ${s.version} · 配置和你上传的皮肤保存在 ${s.app_dir}`;
  }
  async function saveSettings(p) {
    try {
      const d = await PS.post("/api/settings", p);
      settings = d.settings; renderSettings(); saved();
      if ("interval" in p) setTimeout(() => location.reload(), 300);    // 采样间隔变了：重新连接，按新间隔画图
      PS.setCfg(Object.assign({}, PS.state.cfg, { maid: { enabled: settings.bubble } }));
    } catch (err) { alert(err.message); renderSettings(); }
  }
  $("#st-interval").addEventListener("click", e => { const b = e.target.closest("button"); if (b) saveSettings({ interval: +b.dataset.v }); });
  $("#st-autostart").addEventListener("change", e => saveSettings({ autostart: e.target.checked }));
  $("#st-open").addEventListener("change", e => saveSettings({ open_on_start: e.target.checked }));
  $("#st-bubble").addEventListener("change", e => saveSettings({ bubble: e.target.checked }));
  $("#st-topmost").addEventListener("change", e => saveSettings({ mini_topmost: e.target.checked }));
  $("#st-alerts").addEventListener("change", e => saveSettings({ alerts: { enabled: e.target.checked } }));
  $("#st-limits").addEventListener("change", e => { const k = e.target.dataset.k; if (k) saveSettings({ alerts: { [k]: +e.target.value } }); });

  // 其他窗口（例如另一个面板）换了皮肤时，抽屉里的列表跟着更新
  addEventListener("ps:skin", e => { if (sheet.open && e.detail.id !== active) loadSkins(); });
})();
