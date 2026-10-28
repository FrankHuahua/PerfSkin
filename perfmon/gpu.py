"""显卡数据。按顺序尝试：
1. NVML（NVIDIA 显卡，最全：占用、显存、温度、功耗、风扇）
2. Windows 性能计数器 PDH（任何显卡都有：和任务管理器同一个数据源，占用 + 显存）
3. 都不行就返回空列表（界面会显示“未检测到”）
每个读取器的 read() 都返回 list[dict]，字段见 GpuInfo 注释。"""
from __future__ import annotations

import logging
import math
import random
import re
import sys
import time

from . import winsys

log = logging.getLogger(__name__)

# GpuInfo 字段：
#   name: str            显卡名称
#   util: float          总占用 %（0~100）
#   mem_used / mem_total: int  显存（字节），未知为 0
#   temp: float|None     温度 ℃
#   power / power_limit: float|None  功耗 W
#   fan: float|None      风扇 %
#   engines: dict        各引擎占用 {"3D": 12.0, "Copy": 0.4, "Video Decode": 3.1, ...}
#   source: "nvml"|"pdh"|"demo"


class NvmlReader:
    source = "nvml"

    def __init__(self):
        import pynvml  # nvidia-ml-py
        self.n = pynvml
        pynvml.nvmlInit()
        self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(pynvml.nvmlDeviceGetCount())]
        if not self.handles:
            raise RuntimeError("没有 NVIDIA 显卡")
        self.names = []
        for h in self.handles:
            nm = pynvml.nvmlDeviceGetName(h)
            self.names.append(nm.decode() if isinstance(nm, bytes) else nm)

    def _try(self, fn, *a):
        try:
            return fn(*a)
        except Exception:
            return None

    def read(self):
        n, out = self.n, []
        for h, name in zip(self.handles, self.names):
            u = self._try(n.nvmlDeviceGetUtilizationRates, h)
            m = self._try(n.nvmlDeviceGetMemoryInfo, h)
            t = self._try(n.nvmlDeviceGetTemperature, h, 0)  # NVML_TEMPERATURE_GPU
            p = self._try(n.nvmlDeviceGetPowerUsage, h)
            pl = self._try(n.nvmlDeviceGetEnforcedPowerLimit, h)
            f = self._try(n.nvmlDeviceGetFanSpeed, h)
            enc = self._try(n.nvmlDeviceGetEncoderUtilization, h)
            dec = self._try(n.nvmlDeviceGetDecoderUtilization, h)
            engines = {}
            if u is not None:
                engines["3D"] = float(u.gpu)
            if enc:
                engines["Video Encode"] = float(enc[0])
            if dec:
                engines["Video Decode"] = float(dec[0])
            out.append({
                "name": name, "util": float(u.gpu) if u is not None else 0.0,
                "mem_used": int(m.used) if m else 0, "mem_total": int(m.total) if m else 0,
                "temp": float(t) if t is not None else None,
                "power": p / 1000 if p else None, "power_limit": pl / 1000 if pl else None,
                "fan": float(f) if f is not None else None, "engines": engines, "source": self.source,
            })
        return out

    def close(self):
        try:
            self.n.nvmlShutdown()
        except Exception:
            pass


class PdhReader:
    """Windows 性能计数器：\\GPU Engine(*)\\Utilization Percentage 与 \\GPU Adapter Memory(*)\\Dedicated Usage。
    算法与任务管理器一致：同一显卡同一引擎类型的所有进程实例相加，显卡占用 = 各引擎类型里最大的那个。"""
    source = "pdh"
    REQUERY_EVERY = 20.0   # 新进程的引擎实例需要重新建查询才能看到

    def __init__(self):
        if not winsys.IS_WINDOWS:
            raise RuntimeError("PDH 只在 Windows 上可用")
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        pdh = ctypes.WinDLL("pdh")
        H = ctypes.c_void_p
        pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(H)]
        pdh.PdhOpenQueryW.restype = ctypes.c_uint32
        pdh.PdhAddEnglishCounterW.argtypes = [H, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(H)]
        pdh.PdhAddEnglishCounterW.restype = ctypes.c_uint32
        pdh.PdhCollectQueryData.argtypes = [H]
        pdh.PdhCollectQueryData.restype = ctypes.c_uint32
        pdh.PdhCloseQuery.argtypes = [H]
        pdh.PdhCloseQuery.restype = ctypes.c_uint32

        class VALUE(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double),
                            ("largeValue", ctypes.c_longlong)]
            _anonymous_ = ("u",)
            _fields_ = [("CStatus", wintypes.DWORD), ("u", _U)]

        class ITEM(ctypes.Structure):
            _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", VALUE)]

        self.ITEM = ITEM
        pdh.PdhGetFormattedCounterArrayW.argtypes = [H, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_uint32
        self.pdh = pdh
        self.q = None
        self.adapters = winsys.video_adapters()
        self._open()
        time.sleep(0.25)
        self.pdh.PdhCollectQueryData(self.q)

    def _open(self):
        ct = self.ct
        if self.q:
            self.pdh.PdhCloseQuery(self.q)
        q = ct.c_void_p()
        if self.pdh.PdhOpenQueryW(None, 0, ct.byref(q)) != 0:
            raise RuntimeError("PdhOpenQuery 失败")
        self.q = q
        self.c_eng, self.c_mem = ct.c_void_p(), ct.c_void_p()
        r1 = self.pdh.PdhAddEnglishCounterW(q, "\\GPU Engine(*)\\Utilization Percentage", 0, ct.byref(self.c_eng))
        r2 = self.pdh.PdhAddEnglishCounterW(q, "\\GPU Adapter Memory(*)\\Dedicated Usage", 0, ct.byref(self.c_mem))
        if r1 != 0:
            raise RuntimeError(f"系统没有 GPU Engine 计数器 (0x{r1:08X})")
        if r2 != 0:
            self.c_mem = None
        self.pdh.PdhCollectQueryData(q)
        self._opened = time.monotonic()

    def _array(self, counter):
        ct, wt = self.ct, self.wt
        size, count = wt.DWORD(0), wt.DWORD(0)
        fmt = 0x00000200 | 0x00008000  # PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        r = self.pdh.PdhGetFormattedCounterArrayW(counter, fmt, ct.byref(size), ct.byref(count), None)
        if r != 0x800007D2 or not size.value:  # PDH_MORE_DATA
            return {}
        buf = ct.create_string_buffer(size.value)
        r = self.pdh.PdhGetFormattedCounterArrayW(counter, fmt, ct.byref(size), ct.byref(count), buf)
        if r != 0:
            return {}
        items = ct.cast(buf, ct.POINTER(self.ITEM))
        out = {}
        for i in range(count.value):
            it = items[i]
            if it.FmtValue.CStatus in (0, 1):  # PDH_CSTATUS_VALID_DATA / NEW_DATA
                out[it.szName] = out.get(it.szName, 0.0) + it.FmtValue.doubleValue
        return out

    _RE_ENG = re.compile(r"luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_(\d+).*engtype_(.+)$")
    _RE_MEM = re.compile(r"luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_(\d+)")

    def read(self):
        if time.monotonic() - self._opened > self.REQUERY_EVERY:
            self._open()
            time.sleep(0.1)
        self.pdh.PdhCollectQueryData(self.q)
        per = {}   # luid -> {engtype: sum}
        for name, v in self._array(self.c_eng).items():
            m = self._RE_ENG.search(name)
            if not m:
                continue
            luid, eng = m.group(1), m.group(3).strip()
            d = per.setdefault(luid, {})
            d[eng] = d.get(eng, 0.0) + v
        mem = {}
        if self.c_mem:
            for name, v in self._array(self.c_mem).items():
                m = self._RE_MEM.search(name)
                if m:
                    mem[m.group(1)] = mem.get(m.group(1), 0.0) + v
        luids = set(per) | set(mem)
        # 去掉完全空闲且没有显存的虚拟适配器
        luids = [l for l in luids if mem.get(l, 0) > 0 or any(per.get(l, {}).values())] or list(luids)
        luids.sort(key=lambda l: -mem.get(l, 0))
        adapters = sorted(self.adapters, key=lambda a: -a["vram"])
        out = []
        for i, l in enumerate(luids[:3]):
            eng = {k: min(100.0, v) for k, v in per.get(l, {}).items()}
            a = adapters[i] if i < len(adapters) else {"name": "GPU %d" % i, "vram": 0}
            out.append({"name": a["name"], "util": max(eng.values()) if eng else 0.0,
                        "mem_used": int(mem.get(l, 0)), "mem_total": int(a["vram"]),
                        "temp": None, "power": None, "power_limit": None, "fan": None,
                        "engines": dict(sorted(eng.items(), key=lambda kv: -kv[1])[:5]),
                        "source": self.source})
        return out

    def close(self):
        if self.q:
            self.pdh.PdhCloseQuery(self.q)
            self.q = None


class DemoReader:
    source = "demo"

    def __init__(self):
        self.t0 = time.time()
        self.rnd = random.Random(3)

    def read(self):
        t = time.time()   # 用绝对时间，和演示历史曲线（collector.DemoCollector）接得上
        u = max(0.0, min(100.0, 42 + 22 * math.sin(t / 45) + 6 * math.sin(t / 7) + self.rnd.uniform(-3, 3)))
        return [{"name": "NVIDIA GeForce RTX 4070 (演示)", "util": u,
                 "mem_used": int((4.2 + 1.5 * math.sin(t / 30)) * 2**30), "mem_total": 12 * 2**30,
                 "temp": 52 + u * .22, "power": 40 + u * 1.6, "power_limit": 200.0, "fan": 30 + u * .4,
                 "engines": {"3D": u, "Video Decode": 4.0, "Copy": 1.2}, "source": self.source}]

    def close(self):
        pass


def make_reader(demo=False):
    if demo:
        return DemoReader()
    for cls in (NvmlReader, PdhReader):
        try:
            r = cls()
            r.read()
            log.info("显卡数据来源: %s", cls.source)
            return r
        except Exception as e:  # noqa: BLE001
            log.info("显卡读取器 %s 不可用: %s", cls.__name__, e)
    return None


if __name__ == "__main__":  # python -m perfmon.gpu  单独测试
    logging.basicConfig(level=logging.INFO)
    r = make_reader("--demo" in sys.argv)
    for _ in range(3):
        print(r.read() if r else "没有可用的显卡读取器")
        time.sleep(1)
