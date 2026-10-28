/* 极简 Canvas 图表（无依赖）。规则：直线段、不发光、3–5 条淡网格、刻度取整、颜色从 CSS 变量读。
   每秒最多重画一次，窗口隐藏时调用方不画——这是省资源的关键。 */
(function (g) {
  // 颜色变量缓存：换肤时调用 Charts.invalidate() 清空
  let cache = {};
  const v = (name) => cache[name] || (cache[name] = getComputedStyle(document.documentElement).getPropertyValue(name).trim());
  const FONT = '12px Inter, "Microsoft YaHei UI", sans-serif';

  function fit(cv) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  function alpha(color, a) {                     // "#rrggbb" -> rgba
    const n = parseInt(color.slice(1), 16);
    return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
  }

  function polyline(ctx, pts) {
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  }

  function niceMax(v) {                          // 取整的上限：刻度落在 1/2/2.5/5 × 10^n
    if (v <= 0) return 1;
    const e = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * e >= v) return m * e;
    return 10 * e;
  }

  /* 多条百分比曲线。series: [{key, color, values, visible, fill}]，times 为秒级时间戳 */
  function timeline(cv, times, series, opts) {
    opts = opts || {};
    const { ctx, w, h } = fit(cv);
    const L = 44, R = 6, T = 8, B = 22, pw = w - L - R, ph = h - T - B;
    const grid = v("--rule"), label = v("--faint");
    ctx.font = FONT; ctx.textBaseline = "middle"; ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const y = Math.round(T + ph * i / 4) + .5;
      ctx.strokeStyle = grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + pw, y); ctx.stroke();
      ctx.fillStyle = label; ctx.fillText((100 - i * 25) + "%", L - 8, y);
    }
    const n = times.length;
    if (n < 2) return null;
    const t1 = times[n - 1], t0 = t1 - (opts.span || (t1 - times[0]));
    const X = (t) => L + pw * (t - t0) / Math.max(1, t1 - t0);
    const Y = (val) => T + ph * (1 - Math.min(100, Math.max(0, val)) / 100);
    // 时间刻度：步长落在整 1/2/5 分钟
    const span = t1 - t0, step = span > 900 ? 300 : span > 360 ? 120 : 60;
    ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = label;
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
      const x = X(t);
      if (x < L + 18 || x > L + pw - 18) continue;
      ctx.fillText(F.clock(t).slice(0, 5), x, T + ph + 7);
    }
    for (const s of series.slice().reverse()) {     // 主数据最后画，压在最上面
      if (!s.visible) continue;
      const pts = [];
      for (let i = 0; i < n; i++) if (s.values[i] != null && times[i] >= t0) pts.push([X(times[i]), Y(s.values[i])]);
      if (pts.length < 2) continue;
      if (s.fill) {
        polyline(ctx, pts); ctx.lineTo(pts[pts.length - 1][0], T + ph); ctx.lineTo(pts[0][0], T + ph); ctx.closePath();
        ctx.fillStyle = alpha(s.color, .12); ctx.fill();
      }
      polyline(ctx, pts);
      ctx.strokeStyle = s.color; ctx.lineWidth = s.fill ? 2 : 1.5; ctx.lineJoin = "round"; ctx.stroke();
    }
    if (opts.hover != null && opts.hover >= 0 && opts.hover < n) {
      const x = Math.round(X(times[opts.hover])) + .5;
      ctx.strokeStyle = v("--control"); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, T + ph); ctx.stroke();
      for (const s of series) {
        if (!s.visible || s.values[opts.hover] == null) continue;
        ctx.beginPath(); ctx.arc(x, Y(s.values[opts.hover]), 3.5, 0, Math.PI * 2); ctx.fillStyle = s.color; ctx.fill();
      }
    }
    return { L, pw, t0, t1, X };
  }

  /* 网络：下载在上（强调色），上传在下（灰），共用一个取整量程。返回量程（字节/秒） */
  function mirror(cv, rx, tx, n) {
    const { ctx, w, h } = fit(cv);
    n = n || 90;
    const a = rx.slice(-n), b = tx.slice(-n);
    const max = niceMax(Math.max(64 * 1024, ...a, ...b) * 1.1);
    const mid = Math.round(h * .6) + .5, step = w / (n - 1);
    ctx.strokeStyle = v("--rule"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(w, mid); ctx.stroke();
    const draw = (vals, up, color) => {
      const off = (n - vals.length) * step;
      const pts = vals.map((val, i) => [off + i * step, up ? mid - (mid - 2) * Math.min(1, val / max) : mid + (h - mid - 2) * Math.min(1, val / max)]);
      if (pts.length < 2) return;
      polyline(ctx, pts); ctx.lineTo(pts[pts.length - 1][0], mid); ctx.lineTo(pts[0][0], mid); ctx.closePath();
      ctx.fillStyle = alpha(color, .14); ctx.fill();
      polyline(ctx, pts); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
    };
    draw(a, true, v("--accent"));
    draw(b, false, v("--chart3"));
    return max;
  }

  /* 迷你窗的小曲线 */
  function spark(cv, values, color, max, n) {
    const { ctx, w, h } = fit(cv);
    n = n || 60; max = max || 100;
    const vs = values.slice(-n), step = w / (n - 1), off = (n - vs.length) * step;
    const pts = vs.map((val, i) => [off + i * step, 2 + (h - 4) * (1 - Math.min(1, (val || 0) / max))]);
    if (pts.length < 2) return;
    polyline(ctx, pts); ctx.lineTo(pts[pts.length - 1][0], h); ctx.lineTo(pts[0][0], h); ctx.closePath();
    ctx.fillStyle = alpha(color, .14); ctx.fill();
    polyline(ctx, pts); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
  }

  g.Charts = { timeline, mirror, spark, niceMax, alpha, color: v, invalidate: () => { cache = {}; } };
})(window);
