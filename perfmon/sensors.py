"""温度 / 风扇 / 功耗传感器。

这是一个“插槽”：采集器每隔几秒调用所有已启用的 SensorProvider.read()，把结果合并到
snapshot["sensors"]，界面的“传感器”卡片会自动显示。

要接新的数据源（例如 LibreHardwareMonitor），只需要：
  1. 写一个继承 SensorProvider 的类，实现 read()
  2. 在 PROVIDERS 里登记，并在 config.json 的 "sensors" 里启用
不需要改采集器、服务器或界面。
"""
from __future__ import annotations

import logging
import math
import time

log = logging.getLogger(__name__)


def reading(name: str, kind: str, value: float, unit: str, group: str = "", hw: str = "") -> dict:
    """一条传感器读数。
    kind:  "temp"（℃） | "fan"（RPM） | "power"（W） | "load"（%） | "clock"（MHz） | "voltage"（V）
    group: 归属的部件，界面用它分组和挑代表值："cpu" | "gpu" | "board" | "disk" | "other"
    hw:    硬件名称，例如 "AMD Ryzen 7 7800X3D"、"Samsung SSD 990 PRO"
    """
    return {"name": name, "kind": kind, "value": float(value), "unit": unit, "group": group or "other",
            "hw": hw}


class SensorProvider:
    """传感器数据源基类。构造失败（抛异常）表示这台电脑上不可用，会被自动跳过。"""
    name = "base"
    interval = 2.0          # 最短读取间隔（秒）
    wants_config = False    # 为 True 时构造函数会收到整个配置 dict：cls(cfg)

    def read(self) -> list[dict]:
        raise NotImplementedError

    def close(self):
        pass


class PsutilSensors(SensorProvider):
    """psutil 自带的温度/风扇（Linux、部分 macOS 有；Windows 上 psutil 不提供，会构造失败）。"""
    name = "psutil"

    def __init__(self):
        import psutil
        if not hasattr(psutil, "sensors_temperatures"):
            raise RuntimeError("psutil 在本系统不提供温度")
        self.ps = psutil
        if not psutil.sensors_temperatures():
            raise RuntimeError("没有温度传感器")

    def read(self):
        out = []
        for chip, items in (self.ps.sensors_temperatures() or {}).items():
            group = "cpu" if chip in ("coretemp", "k10temp", "zenpower", "cpu_thermal") else \
                "disk" if chip.startswith("nvme") else "other"
            for it in items:
                out.append(reading(it.label or chip, "temp", it.current, "℃", group, chip))
        fans = getattr(self.ps, "sensors_fans", lambda: {})() or {}
        for chip, items in fans.items():
            for it in items:
                out.append(reading(it.label or chip, "fan", it.current, "RPM", "board", chip))
        return out


class DemoSensors(SensorProvider):
    name = "demo"

    def __init__(self):
        self.t0 = time.time()

    def read(self):
        t = time.time() - self.t0
        c = 48 + 12 * math.sin(t / 11)
        return [
            reading("CPU Package", "temp", c, "℃", "cpu", "Demo CPU"),
            reading("CPU Fan", "fan", 900 + c * 9, "RPM", "board", "Demo Board"),
            reading("NVMe Composite", "temp", 41 + 3 * math.sin(t / 23), "℃", "disk", "Demo SSD"),
            reading("Motherboard", "temp", 36.5, "℃", "board", "Demo Board"),
        ]


# 名称 -> 类。LHM 在首次使用时登记，避免传感器模块循环导入。
PROVIDERS: dict[str, type[SensorProvider]] = {
    "psutil": PsutilSensors,
    "demo": DemoSensors,
}


def make_providers(names: list[str], demo=False, cfg: dict | None = None) -> list[SensorProvider]:
    if demo:
        names = ["demo"]
    out = []
    for n in names:
        if n == "lhm" and n not in PROVIDERS:
            try:
                from .sensors_lhm import LhmSensors
                PROVIDERS[n] = LhmSensors
            except Exception as e:  # noqa: BLE001
                log.info("传感器来源 %s 不可用: %s", n, e)
                continue
        cls = PROVIDERS.get(n)
        if not cls:
            log.info("传感器来源 %s 尚未实现，跳过", n)
            continue
        try:
            out.append(cls(cfg or {}) if cls.wants_config else cls())
            log.info("传感器来源已启用: %s", n)
        except Exception as e:  # noqa: BLE001
            log.info("传感器来源 %s 不可用: %s", n, e)
    return out
