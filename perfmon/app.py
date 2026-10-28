"""程序主体：配置、日志、采集器、本地服务、托盘图标和窗口，全部在这里串起来。"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler

from . import winsys
from .collector import Collector, DemoCollector

log = logging.getLogger("perfmon")

DEFAULT_CONFIG = {
    "version": 1,
    "interval": 1.0,             # 采样间隔（秒）
    "history_minutes": 10,       # 图表保留多长时间的历史
    "port": 47821,               # 本地服务端口（被占用会自动换）
    "open_on_start": True,       # 启动时打开监控面板
    "window_size": [1360, 880],
    "sensors": ["lhm", "psutil"],  # 传感器来源（可用的都会启用），见 perfmon/sensors.py
    "skin": "maid",              # 当前皮肤（skins/ 内置，user_skins/ 是你上传的）
    "lhm": {"url": "http://127.0.0.1:8085/data.json"},   # LibreHardwareMonitor 网页服务地址
    "mini": {"size": [340, 176], "topmost": True},         # 迷你悬浮窗
    "net_exclude": [],           # 不计入总流量的网卡名关键字（例如加速器、VPN 的虚拟网卡）
    "maid": {"enabled": True},   # 立绘旁边的对话气泡
    "alerts": {                  # 高负载提醒
        "enabled": False,
        "cpu": 90, "mem": 90, "gpu": 95, "temp": 90, "disk_free_gb": 10,
        "sustain_seconds": 30, "cooldown_minutes": 10,
    },
}


def load_config(path: str) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:  # noqa: BLE001
            log.exception("配置文件读取失败，使用默认配置: %s", path)
            return cfg
    save_json(path, cfg)
    return cfg


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        log.exception("写入失败: %s", path)


def setup_logging(app_dir: str, verbose=False):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = RotatingFileHandler(os.path.join(app_dir, "perfmon.log"), maxBytes=512 * 1024, backupCount=1,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr and not getattr(sys, "frozen", False):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


class App:
    def __init__(self, args):
        self.args = args
        self.dir = winsys.app_dir()
        self.res_dir = winsys.resource_dir()
        self.cache_dir = os.path.join(self.dir, ".cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.profile_dir = os.path.join(self.cache_dir, "edge-profile")   # 专用浏览器配置，放在程序文件夹里
        self.config_path = os.path.join(self.dir, "config.json")
        self.cfg = load_config(self.config_path)
        self.token = secrets.token_urlsafe(18)
        self.stopping = False
        self.features: set[str] = set()     # 可选模块启用后往这里登记，界面据此显示入口
        self.tray = None
        self.extensions: list = []
        from .skins import SkinStore
        self.skins = SkinStore(os.path.join(self.res_dir, "skins"), os.path.join(self.dir, "user_skins"))
        self._skin_switches = 0
        if not self.skins.get(self.cfg.get("skin", "maid")):
            self.cfg["skin"] = "maid"

        cls = DemoCollector if args.demo else Collector
        self.collector = cls(self.cfg, demo=args.demo)
        from .server import Server
        self.server = Server(self, port=args.port or int(self.cfg.get("port", 47821)))
        # 省资源：窗口都关掉时不再遍历进程；传感器只在有人看或开着高负载提醒时读
        self.collector.want_procs = lambda: self.server.clients > 0
        self.collector.want_sensors = lambda: self.server.clients > 0 or bool(self.cfg["alerts"].get("enabled"))
        self._load_extensions()

    # ------------------------------------------------------------ 皮肤
    @property
    def skin_version(self) -> int:
        """换肤或编辑皮肤后变化；SSE 据此通知页面刷新。"""
        return self.skins.version * 100000 + self._skin_switches

    def active_skin(self) -> dict:
        from .skins import build_palette
        return self.skins.get(self.cfg.get("skin", "maid")) or self.skins.get("maid") or {
            "id": "fallback", "name": "默认", "palette": build_palette("#d97757"), "layout": "none"}

    def set_skin(self, sid: str):
        if self.cfg.get("skin") != sid:
            self.cfg["skin"] = sid
            save_json(self.config_path, self.cfg)
        self._skin_switches += 1

    def skin_public(self, sid: str | None = None) -> dict:
        from .skins import public
        skin = self.skins.get(sid) if sid else self.active_skin()
        return public(skin or self.active_skin(), "/skins/")

    def skin_css(self) -> str:
        from .skins import skin_css
        skin = self.active_skin()
        return skin_css(skin, f"/skins/{skin['id']}/")

    # ------------------------------------------------------------ 设置（界面里的“设置”页）
    INTERVALS = (0.5, 1.0, 2.0, 5.0)

    def settings(self) -> dict:
        c = self.cfg
        return {
            "interval": c.get("interval", 1.0), "intervals": list(self.INTERVALS),
            "open_on_start": c.get("open_on_start", True), "close_window_on_exit": c.get("close_window_on_exit", True),
            "bubble": c.get("maid", {}).get("enabled", True), "mini_topmost": c["mini"].get("topmost", True),
            "autostart": winsys.autostart_enabled(), "autostart_supported": winsys.IS_WINDOWS,
            "alerts": dict(c["alerts"]), "version": __import__("perfmon").__version__,
            "app_dir": self.dir,
        }

    def update_settings(self, p: dict) -> dict:
        """只接受白名单里的键；数值做范围检查。改完立即生效并写回 config.json。"""
        c = self.cfg

        def num(v, lo, hi):
            v = float(v)
            if not lo <= v <= hi:
                raise ValueError(f"数值超出范围 {lo}–{hi}")
            return v

        if "interval" in p:
            v = float(p["interval"])
            if v not in self.INTERVALS:
                raise ValueError("采样间隔只能是 0.5 / 1 / 2 / 5 秒")
            c["interval"] = v
            self.collector.interval = v
            self.collector.info["interval"] = v
        for key, cfg_key in (("open_on_start", "open_on_start"), ("close_window_on_exit", "close_window_on_exit")):
            if key in p:
                c[cfg_key] = bool(p[key])
        if "bubble" in p:
            c.setdefault("maid", {})["enabled"] = bool(p["bubble"])
        if "mini_topmost" in p:
            c["mini"]["topmost"] = bool(p["mini_topmost"])
        if "autostart" in p and winsys.IS_WINDOWS:
            winsys.set_autostart(bool(p["autostart"]))
        a = p.get("alerts")
        if isinstance(a, dict):
            limits = {"cpu": (10, 100), "mem": (10, 100), "gpu": (10, 100), "temp": (40, 110),
                      "disk_free_gb": (1, 500), "sustain_seconds": (5, 600), "cooldown_minutes": (1, 240)}
            for k, (lo, hi) in limits.items():
                if k in a:
                    c["alerts"][k] = num(a[k], lo, hi)        # 原地修改：提醒扩展持有的是同一个 dict
            if "enabled" in a:
                c["alerts"]["enabled"] = bool(a["enabled"])
        save_json(self.config_path, c)
        if self.tray:
            try:
                self.tray.update_menu()
            except Exception:  # noqa: BLE001
                pass
        return self.settings()

    def end_process(self, name: str) -> dict:
        from .procs import end_by_name
        return end_by_name(name, profile_marker=self.profile_dir)

    # ------------------------------------------------------------ 可选扩展（perfmon/ext_*.py）
    def _load_extensions(self):
        """perfmon/ext_*.py 里如果有 setup(app) 函数，就调用它。出错只记日志，不影响主程序。"""
        import importlib
        import pkgutil
        from . import __path__ as pkg_path
        for m in sorted(pkgutil.iter_modules(pkg_path), key=lambda m: m.name):
            if not m.name.startswith("ext_"):
                continue
            try:
                mod = importlib.import_module(f"perfmon.{m.name}")
                ext = mod.setup(self)
                if ext is not None:
                    self.extensions.append(ext)
                log.info("扩展已加载: %s", m.name)
            except Exception:  # noqa: BLE001
                log.exception("扩展 %s 加载失败", m.name)

    # ------------------------------------------------------------ 动作（托盘菜单、页面按钮、第二个实例都走这里）
    def action(self, name: str, payload: dict | None = None) -> bool:
        payload = payload or {}
        if name == "open-window":
            self.open_window()
        elif name == "open-config":
            winsys.open_path(self.config_path)
        elif name == "open-log":
            winsys.open_path(os.path.join(self.dir, "perfmon.log"))
        elif name == "open-folder":
            winsys.open_path(self.dir)
        else:
            for ext in self.extensions:
                fn = getattr(ext, "action", None)
                if fn and fn(name, payload):
                    return True
            return False
        return True

    def open_window(self, page: str = "", size=None, title=winsys.WINDOW_TITLE):
        size = size or tuple(self.cfg.get("window_size", (1360, 880)))
        winsys.open_app_window(self.server.url + page, self.profile_dir, size, title)

    # ------------------------------------------------------------ 托盘
    def _make_tray(self):
        try:
            import pystray
        except ImportError:
            log.warning("没有安装 pystray / Pillow，不显示托盘图标")
            return None
        from .icon import tray_image

        def item(text, fn, **kw):
            return pystray.MenuItem(text, lambda *_: fn(), **kw)

        def toggle_autostart():
            winsys.set_autostart(not winsys.autostart_enabled())

        extra = []
        for ext in self.extensions:
            try:
                for it in getattr(ext, "menu_items", lambda: [])():
                    if len(it) >= 3:      # (文字, 回调, 是否勾选的函数) → 带勾选标记的菜单项
                        text, fn, chk = it[:3]
                        extra.append(pystray.MenuItem(text, (lambda f: lambda *_: f())(fn),
                                                      checked=(lambda c: lambda _: bool(c()))(chk)))
                    else:
                        extra.append(item(it[0], it[1]))
            except Exception:  # noqa: BLE001
                log.exception("扩展菜单构建失败")
        menu = pystray.Menu(
            item("打开监控面板", self.open_window, default=True),
            *extra,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("开机自动启动", lambda *_: toggle_autostart(),
                             checked=lambda _: winsys.autostart_enabled()),
            item("编辑配置文件", lambda: self.action("open-config")),
            item("打开程序文件夹", lambda: self.action("open-folder")),
            pystray.Menu.SEPARATOR,
            item("退出", self.quit),
        )
        icon = pystray.Icon(winsys.APP_NAME, tray_image(64, self.active_skin()["palette"]["accent"]),
                            "PerfSkin 性能监控", menu)
        threading.Thread(target=self._tooltip_loop, daemon=True, name="tooltip").start()
        return icon

    def _tooltip_loop(self):
        while not self.stopping:
            time.sleep(2)
            s = self.collector.latest()
            if not s or not self.tray:
                continue
            parts = [f"CPU {s['cpu']['percent']:.0f}%", f"内存 {s['mem']['percent']:.0f}%"]
            if s["gpu"]:
                parts.append(f"GPU {s['gpu'][0]['util']:.0f}%")
            try:
                self.tray.title = "PerfSkin\n" + " · ".join(parts)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ 生命周期
    def run(self):
        self.collector.start()
        self.server.start()
        save_json(os.path.join(self.cache_dir, "instance.json"),
                  {"port": self.server.port, "token": self.token, "pid": os.getpid()})
        for ext in self.extensions:
            getattr(ext, "start", lambda: None)()
        if not self.args.background and self.cfg.get("open_on_start", True) and not self.args.no_window:
            threading.Timer(0.6, self.open_window).start()
        self.tray = None if self.args.no_tray else self._make_tray()
        if self.tray:
            self.tray.run()          # 阻塞，直到点“退出”
        else:
            print(f"性能监控已启动：{self.server.url}  （Ctrl+C 退出）")
            try:
                while not self.stopping:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
        self.quit()

    def quit(self):
        if self.stopping:
            return
        self.stopping = True
        log.info("退出")
        for ext in self.extensions:
            try:
                getattr(ext, "stop", lambda: None)()
            except Exception:  # noqa: BLE001
                log.exception("扩展停止失败")
        self.collector.stop()
        self.server.stop()
        if self.tray:
            self.tray.stop()
        if self.cfg.get("close_window_on_exit", True):
            winsys.close_app_windows(self.profile_dir)


def ask_running_instance(app_dir: str, action="open-window") -> bool:
    """已经有一个在运行：让它打开窗口，然后自己退出。"""
    try:
        with open(os.path.join(app_dir, ".cache", "instance.json"), encoding="utf-8") as f:
            inst = json.load(f)
        req = urllib.request.Request(f"http://127.0.0.1:{inst['port']}/api/action/{action}", data=b"{}",
                                     headers={"X-Token": inst["token"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="PerfSkin —— 可以换皮肤的 Windows 性能监控")
    ap.add_argument("--demo", action="store_true", help="演示数据（显卡、传感器、磁盘用模拟值）")
    ap.add_argument("--no-tray", action="store_true", help="不显示托盘图标，在控制台运行")
    ap.add_argument("--no-window", action="store_true", help="启动时不打开窗口")
    ap.add_argument("--background", action="store_true", help="开机自启用：只在托盘里待命")
    ap.add_argument("--replace", action="store_true", help="如果已经在运行，先结束旧的再启动")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--once", action="store_true", help="采样一次并打印 JSON，用于检查")
    ap.add_argument("--check", action="store_true", help="打印检测到的硬件和数据来源")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    app_dir = winsys.app_dir()
    setup_logging(app_dir, args.verbose)

    if args.once:
        cfg = load_config(os.path.join(app_dir, "config.json"))
        c = (DemoCollector if args.demo else Collector)(cfg, demo=args.demo)
        c.sample()
        time.sleep(1.0)
        snap = c.sample()
        snap.pop("h", None)
        print(json.dumps({"info": c.info, "snapshot": snap}, ensure_ascii=False, indent=1, default=str))
        c.stop()
        return 0

    if args.check:
        cfg = load_config(os.path.join(app_dir, "config.json"))
        c = Collector(cfg)
        c.sample()
        time.sleep(1.0)
        s = c.sample()
        i = c.info
        print(f"  主机     {i['host']}  ({i['os']})")
        print(f"  处理器   {i['cpu_name']}  {i['cores']} 核 / {i['threads']} 线程   当前 {s['cpu']['percent']:.0f}%")
        print(f"  内存     {s['mem']['total'] / 2**30:.1f} GB   已用 {s['mem']['percent']:.0f}%")
        if s["gpu"]:
            for g in s["gpu"]:
                print(f"  显卡     {g['name']}   占用 {g['util']:.0f}%   来源 {g['source'].upper()}")
        else:
            print("  显卡     未检测到（NVIDIA 显卡请确认驱动已安装）")
        for v in s["disk"]["volumes"]:
            print(f"  磁盘     {v['mount']}  {v['total'] / 2**30:.0f} GB   已用 {v['percent']:.0f}%")
        nics = [n["name"] for n in s["net"]["nics"] if not n["virtual"]][:3]
        print(f"  网卡     {', '.join(nics) or '—'}")
        temps = [x for x in s["sensors"] if x["kind"] == "temp"]
        print(f"  传感器   {len(temps)} 个温度读数" + ("" if temps else "（Windows 上需要 LibreHardwareMonitor，见 README 的“温度与风扇”）"))
        c.stop()
        return 0

    if args.replace:
        n = winsys.stop_other_instances()
        if n:
            log.info("已结束 %d 个旧实例", n)
            time.sleep(0.8)
    if not winsys.single_instance():
        if ask_running_instance(app_dir):
            return 0
        time.sleep(1.5)                          # 旧实例可能正在退出，再试一次
        if not winsys.single_instance():
            log.warning("已经在运行，但联系不上它")
            return 1
    App(args).run()
    return 0
