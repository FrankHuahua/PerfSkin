"""采集器：后台线程按固定间隔读取系统数据，生成快照（snapshot）并保留一段历史。

节奏（以 interval=1s 为例）：
  每 1 次   CPU / 内存 / 显卡 / 网络 / 磁盘读写
  每 2 次   进程排行（遍历全部进程比较耗时）
  每 2 秒   传感器
  每 15 秒  分区容量、电池
其他模块只通过 latest() / history() / wait_next() 读取，不直接碰 psutil。
"""
from __future__ import annotations

import collections
import logging
import math
import os
import platform
import random
import socket
import threading
import time

import psutil

from . import gpu as gpu_mod
from . import sensors as sensors_mod
from . import winsys
from .procs import is_protected

log = logging.getLogger(__name__)

VIRTUAL_NIC = ("loopback", "vethernet", "vmware", "virtualbox", "isatap", "teredo", "npcap", "pseudo",
               "bluetooth", "wsl", "hyper-v", "docker", "veth", "br-", "本地连接*", "local area connection*")
HISTORY_FIELDS = ("t", "cpu", "mem", "gpu", "vram", "rx", "tx", "dr", "dw", "disk")


def _is_virtual(name: str, extra=()) -> bool:
    n = name.lower()
    if n == "lo":
        return True
    return any(n.startswith(p) or (len(p) > 3 and p in n) for p in VIRTUAL_NIC + tuple(extra))


class Collector(threading.Thread):
    def __init__(self, cfg: dict, demo=False):
        super().__init__(daemon=True, name="collector")
        self.cfg = cfg
        self.demo = demo
        self.interval = max(0.5, float(cfg.get("interval", 1.0)))
        n = int(cfg.get("history_minutes", 10) * 60 / self.interval)
        self._hist: collections.deque = collections.deque(maxlen=max(60, n))
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._snap: dict | None = None
        self._seq = 0
        self._stop = threading.Event()
        self.net_exclude = [s.lower() for s in cfg.get("net_exclude", [])]

        self.gpu = gpu_mod.make_reader(demo)
        self.sensors = sensors_mod.make_providers(cfg.get("sensors", ["lhm", "psutil"]), demo, cfg)
        self._sensor_cache: list = []
        self._sensor_at = 0.0
        self._procs: dict[int, psutil.Process] = {}
        self._proc_cache = {"cpu": [], "mem": [], "count": 0}
        self._vol_cache: list = []
        self._bat_cache = None
        self._slow_at = 0.0
        self._prev_net = None
        self._prev_disk = None
        self._prev_t = None
        self._tick = 0
        self._rnd = random.Random(1)
        # 省电：没人看界面时，跳过最费时的“遍历全部进程”和传感器读取（由 App 根据连接数 / 提醒开关决定）
        self.want_procs = lambda: True
        self.want_sensors = lambda: True
        self._me = psutil.Process()
        self._me.cpu_percent(None)
        self._ncpu = psutil.cpu_count() or 1

        self.info = {
            "host": socket.gethostname(),
            "os": winsys.os_name(),
            "cpu_name": winsys.cpu_name(),
            "cores": psutil.cpu_count(logical=False) or 0,
            "threads": psutil.cpu_count(logical=True) or 0,
            "mem_total": psutil.virtual_memory().total,
            "boot": psutil.boot_time(),
            "interval": self.interval,
            "history_len": self._hist.maxlen,
            "gpu_source": self.gpu.source if self.gpu else None,
            "sensor_sources": [s.name for s in self.sensors],
            "python": platform.python_version(),
            "demo": demo,
        }
        psutil.cpu_percent(percpu=True)  # 预热：第一次调用没有意义

    # ------------------------------------------------------------ 对外接口
    def latest(self):
        with self._lock:
            return self._snap

    def history(self):
        with self._lock:
            return list(self._hist)

    def wait_next(self, seq: int, timeout=5.0):
        """阻塞到出现比 seq 更新的快照，返回 (seq, snapshot)。"""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > seq or self._stop.is_set(), timeout)
            return self._seq, self._snap

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self.gpu:
            self.gpu.close()
        for s in self.sensors:
            s.close()

    # ------------------------------------------------------------ 主循环
    def run(self):
        nxt = time.monotonic() + self.interval
        while not self._stop.is_set():
            delay = nxt - time.monotonic()
            if delay > 0 and self._stop.wait(delay):
                break
            nxt += self.interval
            if nxt < time.monotonic():           # 电脑睡眠后醒来，别追帧
                nxt = time.monotonic() + self.interval
            try:
                snap = self.sample()
            except Exception:
                log.exception("采样失败")
                continue
            with self._cond:
                self._snap = snap
                self._seq += 1
                self._hist.append(snap["h"])
                self._cond.notify_all()

    # ------------------------------------------------------------ 采样
    def sample(self) -> dict:
        now = time.time()
        mono = time.monotonic()
        dt = (mono - self._prev_t) if self._prev_t else self.interval
        self._prev_t = mono
        self._tick += 1

        cpu = self._cpu()
        mem = self._mem()
        gpus = self._gpu()
        net = self._net(dt)
        disk = self._disk(dt)
        if not self.want_procs():
            if self._procs:
                self._procs.clear()                        # 下次恢复时重新计时，避免把空闲期平均进来
            self._proc_cache = {"cpu": [], "mem": [], "count": cpu["procs"], "paused": True}
        elif self._tick % 2 == 1 or not self._proc_cache["cpu"]:
            self._proc_cache = self._processes()
        if mono - self._sensor_at >= 2.0 and self.want_sensors():
            self._sensor_at = mono
            self._sensor_cache = self._read_sensors()
        if mono - self._slow_at >= 15.0 or not self._vol_cache:
            self._slow_at = mono
            self._vol_cache = self._volumes()
            self._bat_cache = self._battery()
        disk["volumes"] = self._vol_cache

        g0 = gpus[0] if gpus else None
        h = {
            "t": round(now, 2), "cpu": round(cpu["percent"], 1), "mem": round(mem["percent"], 1),
            "gpu": round(g0["util"], 1) if g0 else None,
            "vram": round(100 * g0["mem_used"] / g0["mem_total"], 1) if g0 and g0["mem_total"] else None,
            "rx": int(net["rx"]), "tx": int(net["tx"]), "dr": int(disk["read"]), "dw": int(disk["write"]),
            "disk": round(disk["busy"], 1),
        }
        return {
            "t": now, "cpu": cpu, "mem": mem, "gpu": gpus, "net": net, "disk": disk,
            "procs": self._proc_cache, "sensors": self._sensor_cache, "battery": self._bat_cache,
            "uptime": now - self.info["boot"], "h": h, "self": self._self_usage(),
        }

    def _self_usage(self):
        """本程序自己的占用（后台进程，不含浏览器窗口）。"""
        try:
            return {"cpu": round(self._me.cpu_percent(None) / self._ncpu, 2), "mem": self._me.memory_info().rss}
        except psutil.Error:
            return None

    def _cpu(self):
        per = psutil.cpu_percent(percpu=True)
        try:
            f = psutil.cpu_freq()
        except Exception:  # noqa: BLE001
            f = None
        try:
            st = psutil.cpu_stats()
            ctx = st.ctx_switches
        except Exception:  # noqa: BLE001
            ctx = 0
        try:
            nproc = len(psutil.pids())
        except Exception:  # noqa: BLE001
            nproc = 0
        load = None
        if hasattr(os, "getloadavg") and not winsys.IS_WINDOWS:
            load = [round(x, 2) for x in os.getloadavg()]
        return {"percent": sum(per) / len(per) if per else 0.0, "per_core": [round(p, 1) for p in per],
                "freq": round(f.current) if f else None, "freq_max": round(f.max) if f and f.max else None,
                "procs": nproc, "ctx": ctx, "load": load}

    def _mem(self):
        v = psutil.virtual_memory()
        s = psutil.swap_memory()
        return {"total": v.total, "used": v.total - v.available, "available": v.available,
                "percent": v.percent, "swap_total": s.total, "swap_used": s.used, "swap_percent": s.percent}

    def _gpu(self):
        if not self.gpu:
            return []
        try:
            return self.gpu.read()
        except Exception:  # noqa: BLE001
            log.exception("读取显卡失败，停用显卡监控")
            self.gpu = None
            return []

    def _net(self, dt):
        try:
            cur = psutil.net_io_counters(pernic=True)
            stats = psutil.net_if_stats()
        except Exception:  # noqa: BLE001
            return {"rx": 0, "tx": 0, "nics": [], "rx_total": 0, "tx_total": 0}
        prev, self._prev_net = self._prev_net, cur
        nics, rx, tx, rxt, txt = [], 0.0, 0.0, 0, 0
        for name, c in cur.items():
            st = stats.get(name)
            virtual = _is_virtual(name, self.net_exclude)
            r = t = 0.0
            if prev and name in prev:
                r = max(0, c.bytes_recv - prev[name].bytes_recv) / dt
                t = max(0, c.bytes_sent - prev[name].bytes_sent) / dt
            if not virtual:
                rx += r
                tx += t
                rxt += c.bytes_recv
                txt += c.bytes_sent
            if (st and st.isup) or r or t:
                nics.append({"name": name, "rx": r, "tx": t, "up": bool(st and st.isup),
                             "speed": st.speed if st else 0, "virtual": virtual,
                             "rx_total": c.bytes_recv, "tx_total": c.bytes_sent})
        nics.sort(key=lambda n: (n["virtual"], -(n["rx"] + n["tx"]), -(n["rx_total"] + n["tx_total"])))
        return {"rx": rx, "tx": tx, "nics": nics[:6], "rx_total": rxt, "tx_total": txt}

    def _disk(self, dt):
        try:
            cur = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:  # noqa: BLE001
            cur = {}
        prev, self._prev_disk = self._prev_disk, cur
        phys, R, W, busy_max = [], 0.0, 0.0, 0.0
        for name, c in cur.items():
            if not winsys.IS_WINDOWS and (name.startswith(("loop", "ram", "dm-", "zram")) or name[-1:].isdigit()
                                          and not name.startswith("nvme") and not name.startswith("mmcblk")):
                continue   # Linux：跳过 loop 设备和 sda1 这种分区，只统计整块盘
            if not winsys.IS_WINDOWS and name.startswith("nvme") and "p" in name[4:]:
                continue
            r = w = busy = 0.0
            if prev and name in prev:
                p = prev[name]
                r = max(0, c.read_bytes - p.read_bytes) / dt
                w = max(0, c.write_bytes - p.write_bytes) / dt
                bt = getattr(c, "busy_time", None)
                if bt is not None:
                    busy = (bt - p.busy_time) / (dt * 1000) * 100
                else:
                    busy = ((c.read_time - p.read_time) + (c.write_time - p.write_time)) / (dt * 1000) * 100
            busy = max(0.0, min(100.0, busy))
            R += r
            W += w
            busy_max = max(busy_max, busy)
            phys.append({"name": name, "read": r, "write": w, "busy": busy})
        phys.sort(key=lambda d: d["name"])
        return {"read": R, "write": W, "busy": busy_max, "physical": phys}

    def _volumes(self):
        out = []
        try:
            parts = psutil.disk_partitions(all=False)
        except Exception:  # noqa: BLE001
            return out
        seen = set()
        for p in parts:
            if winsys.IS_WINDOWS:
                if "cdrom" in p.opts or not p.fstype:
                    continue
            elif p.fstype in ("squashfs", "tmpfs", "overlay") or p.mountpoint.startswith(("/snap", "/boot/efi")):
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            if p.device in seen or u.total < 1 << 30:
                continue
            seen.add(p.device)
            out.append({"mount": p.mountpoint, "device": p.device, "fstype": p.fstype,
                        "total": u.total, "used": u.used, "percent": u.percent,
                        "removable": "removable" in p.opts})
        out.sort(key=lambda v: v["mount"])
        return out[:8]

    def _processes(self):
        ncpu = psutil.cpu_count() or 1
        seen, groups = set(), {}
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            pid = p.info["pid"]
            if pid in (0,):   # System Idle Process
                continue
            seen.add(pid)
            proc = self._procs.get(pid)
            if proc is None:
                self._procs[pid] = proc = p
                try:
                    proc.cpu_percent(None)
                except psutil.Error:
                    pass
                c = 0.0
            else:
                try:
                    c = proc.cpu_percent(None) / ncpu
                except psutil.Error:
                    c = 0.0
            name = p.info["name"] or f"pid {pid}"
            mi = p.info["memory_info"]
            m = mi.rss if mi else 0
            g = groups.setdefault(name, {"name": name, "count": 0, "cpu": 0.0, "mem": 0, "pid": pid,
                                         "protected": is_protected(name)})
            g["count"] += 1
            g["cpu"] += c
            g["mem"] += m
        for pid in list(self._procs):
            if pid not in seen:
                del self._procs[pid]
        gl = list(groups.values())
        for g in gl:
            g["cpu"] = round(min(100.0, g["cpu"]), 1)
        top_cpu = sorted(gl, key=lambda g: (-g["cpu"], -g["mem"]))[:15]
        top_mem = sorted(gl, key=lambda g: -g["mem"])[:15]
        return {"cpu": top_cpu, "mem": top_mem, "count": len(seen)}

    def _read_sensors(self):
        out = []
        for s in list(self.sensors):
            try:
                out.extend(s.read())
            except Exception:  # noqa: BLE001
                log.exception("传感器 %s 读取失败，已停用", s.name)
                self.sensors.remove(s)
        # 显卡温度也并进来，方便界面统一展示
        snap_g = self._gpu_temp_cache()
        out.extend(snap_g)
        return out

    def _gpu_temp_cache(self):
        if not self.gpu or not self._snap:
            return []
        out = []
        for g in self._snap.get("gpu", []):
            if g.get("temp") is not None:
                out.append(sensors_mod.reading("GPU Core", "temp", g["temp"], "℃", "gpu", g["name"]))
            if g.get("fan") is not None:
                out.append(sensors_mod.reading("GPU Fan", "fan", g["fan"], "%", "gpu", g["name"]))
            if g.get("power") is not None:
                out.append(sensors_mod.reading("GPU Power", "power", g["power"], "W", "gpu", g["name"]))
        return out

    def _battery(self):
        try:
            b = psutil.sensors_battery()
        except Exception:  # noqa: BLE001
            b = None
        if not b:
            return None
        left = b.secsleft if isinstance(b.secsleft, (int, float)) and b.secsleft >= 0 else None
        return {"percent": b.percent, "plugged": bool(b.power_plugged), "secs_left": left}


# ---------------------------------------------------------------- 演示数据（--demo）：让界面在任何电脑上都有起伏
class DemoCollector(Collector):
    """演示模式：一台虚构的游戏电脑，方便截图和调界面（真实的网卡数据照常采集，进程是虚构的）。"""

    # (名字, 数量, 基础 CPU%, 内存 MB)
    FAKE_PROCS = [
        ("Cyberpunk2077.exe", 1, 18.0, 7350), ("chrome.exe", 32, 3.4, 3480), ("Code.exe", 12, 1.6, 1210),
        ("steamwebhelper.exe", 6, 0.9, 640), ("Discord.exe", 6, 0.7, 520), ("msedgewebview2.exe", 8, 0.4, 410),
        ("explorer.exe", 1, 0.3, 180), ("WeChat.exe", 3, 0.3, 350), ("cloudmusic.exe", 4, 0.5, 290),
        ("dwm.exe", 1, 1.1, 120), ("NVIDIA Overlay.exe", 2, 0.2, 140), ("OneDrive.exe", 1, 0.1, 95),
        ("PerfSkin.exe", 1, 0.2, 28), ("System", 1, 0.4, 2), ("csrss.exe", 2, 0.1, 6), ("svchost.exe", 78, 0.6, 980),
    ]

    def __init__(self, cfg: dict, demo=True):
        super().__init__(cfg, demo=True)
        self.info.update(host="DEMO-PC", os="Windows 11 24H2", cpu_name="Intel(R) Core(TM) i5-14600KF",
                         cores=14, threads=20, mem_total=32 * 2**30)
        # 预先填满历史，打开就能看到完整的 10 分钟曲线
        now = time.time()
        n = self._hist.maxlen
        for i in range(n, 0, -1):
            self._hist.append(self._demo_h(now - i * self.interval))

    @staticmethod
    def _wave(t):
        return 32 + 16 * math.sin(t / 70) + 7 * math.sin(t / 13) + 4 * math.sin(t / 2.3)

    def _demo_h(self, t):
        k = self._rnd
        cpu = max(2, min(100, self._wave(t) + k.uniform(-4, 4)))
        mem = 46 + 5 * math.sin(t / 40) + k.uniform(-.3, .3)
        gpu = max(0.0, min(100.0, 42 + 22 * math.sin(t / 45) + 6 * math.sin(t / 7) + k.uniform(-3, 3)))
        rx = max(0, 2.4e6 + 2.2e6 * math.sin(t / 5) + k.uniform(0, 4e5))
        tx = max(0, 3.2e5 + 2.5e5 * math.sin(t / 4 + 1))
        dr = max(0, 1.8e7 * max(0, math.sin(t / 6)) + k.uniform(0, 2e5))
        dw = max(0, 6e6 * max(0, math.sin(t / 8 + 2)) + k.uniform(0, 1e5))
        return {"t": round(t, 2), "cpu": round(cpu, 1), "mem": round(mem, 1), "gpu": round(gpu, 1),
                "vram": round(100 * (4.2 + 1.5 * math.sin(t / 30)) / 12, 1), "rx": int(rx), "tx": int(tx),
                "dr": int(dr), "dw": int(dw), "disk": round(min(100, (dr + dw) / 3.5e5), 1)}

    def _processes(self):
        t = time.time()
        k = self._rnd
        cpu_now = max(5.0, self._wave(t))
        out = []
        for i, (name, count, c, m) in enumerate(self.FAKE_PROCS):
            jitter = 1 + 0.35 * math.sin(t / 5 + i) + k.uniform(-.1, .1)
            cpu = c * jitter * (cpu_now / 30 if i == 0 else 1)
            out.append({"name": name, "count": count, "cpu": round(max(0.0, min(100.0, cpu)), 1),
                        "mem": int(m * (1 + .03 * math.sin(t / 20 + i)) * 2**20), "pid": 1000 + i * 4,
                        "protected": is_protected(name)})
        return {"cpu": sorted(out, key=lambda g: -g["cpu"])[:15], "mem": sorted(out, key=lambda g: -g["mem"])[:15],
                "count": 287}

    def sample(self):
        snap = super().sample()
        t = time.time()
        k = self._rnd
        wave = self._wave(t) + k.uniform(-4, 4)
        n = 20
        snap["cpu"]["per_core"] = [round(max(1, min(100, wave + 25 * math.sin(t / 3 + i) + k.uniform(-8, 8))), 1)
                                   for i in range(n)]
        snap["cpu"]["percent"] = sum(snap["cpu"]["per_core"]) / n
        snap["cpu"]["procs"] = 287
        mp = 46 + 5 * math.sin(t / 40) + k.uniform(-.3, .3)
        tot = 32 * 2**30
        snap["mem"].update(total=tot, percent=round(mp, 1), used=int(tot * mp / 100), available=int(tot * (1 - mp / 100)))
        snap["cpu"]["freq"] = int(3800 + 900 * math.sin(t / 5))
        snap["cpu"]["freq_max"] = 5400
        snap["net"]["rx"] = max(0, 2.4e6 + 2.2e6 * math.sin(t / 5) + k.uniform(0, 4e5))
        snap["net"]["tx"] = max(0, 3.2e5 + 2.5e5 * math.sin(t / 4 + 1))
        if snap["net"]["nics"]:
            snap["net"]["nics"][0].update(rx=snap["net"]["rx"], tx=snap["net"]["tx"], name="以太网")
        snap["disk"]["read"] = max(0, 1.8e7 * max(0, math.sin(t / 6)) + k.uniform(0, 2e5))
        snap["disk"]["write"] = max(0, 6e6 * max(0, math.sin(t / 8 + 2)) + k.uniform(0, 1e5))
        snap["disk"]["busy"] = min(100, (snap["disk"]["read"] + snap["disk"]["write"]) / 3.5e5)
        snap["disk"]["volumes"] = [
            {"mount": "C:\\", "device": "C:", "fstype": "NTFS", "total": 500 * 2**30, "used": 312 * 2**30,
             "percent": 62.4, "removable": False},
            {"mount": "D:\\", "device": "D:", "fstype": "NTFS", "total": 2000 * 2**30, "used": 1530 * 2**30,
             "percent": 76.5, "removable": False},
            {"mount": "E:\\", "device": "E:", "fstype": "NTFS", "total": 1000 * 2**30, "used": 921 * 2**30,
             "percent": 92.1, "removable": False}]
        snap["disk"]["physical"] = [{"name": "PhysicalDrive0", "read": snap["disk"]["read"],
                                     "write": snap["disk"]["write"], "busy": snap["disk"]["busy"]}]
        h = snap["h"]
        h.update(cpu=round(snap["cpu"]["percent"], 1), mem=snap["mem"]["percent"], rx=int(snap["net"]["rx"]), tx=int(snap["net"]["tx"]),
                 dr=int(snap["disk"]["read"]), dw=int(snap["disk"]["write"]), disk=round(snap["disk"]["busy"], 1))
        return snap
