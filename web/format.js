/* 数字格式化（主面板和迷你悬浮窗共用） */
(function (g) {
  const U = ["B", "KB", "MB", "GB", "TB"];
  function bytes(n, d) {               // 12.4 GB
    if (n == null || isNaN(n)) return ["–", ""];
    let i = 0; n = Math.max(0, n);
    while (n >= 1024 && i < U.length - 1) { n /= 1024; i++; }
    const dec = d != null ? d : (n >= 100 || i === 0 ? 0 : 1);
    return [n.toFixed(dec), U[i]];
  }
  function rate(n) {                   // 2.4 MB/s（小于 1 KB/s 也显示 KB）
    if (n == null || isNaN(n)) return ["–", "KB/s"];
    let v = n / 1024, i = 1;
    while (v >= 1000 && i < U.length - 1) { v /= 1024; i++; }
    return [v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2), U[i] + "/s"];
  }
  function pct(n, d) { return n == null || isNaN(n) ? "–" : (+n).toFixed(d == null ? 0 : d); }
  function dur(sec) {                  // 3 天 04:12
    sec = Math.max(0, Math.floor(sec || 0));
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
    const hm = String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
    return d ? d + " 天 " + hm : hm;
  }
  function clock(t) {
    const d = new Date(t * 1000);
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map(x => String(x).padStart(2, "0")).join(":");
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  // PerfSkin 的星芒标志（12 芒圆角星）路径，主面板、迷你窗、图标共用
  function sparkPath() {
    let d = "";
    for (let i = 0; i < 12; i++) {
      const a = i / 12 * Math.PI * 2, L = i % 2 ? 34 : 46, w = .09;
      const p = (r, t) => (Math.cos(t) * r).toFixed(2) + " " + (Math.sin(t) * r).toFixed(2);
      d += `M${p(6, a - w * 2.2)} L${p(L, a - w * .35)} Q${p(L + 5, a)} ${p(L, a + w * .35)} L${p(6, a + w * 2.2)} Z `;
    }
    return d + "M0 -10 A10 10 0 1 1 0 10 A10 10 0 1 1 0 -10Z";
  }
  g.F = { bytes, rate, pct, dur, clock, esc, sparkPath };
})(window);
