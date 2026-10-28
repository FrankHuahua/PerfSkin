"""高负载提醒的判定规则；只处理快照和传入的时间。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Alert:
    key: str
    title: str
    message: str


@dataclass
class _State:
    since: Optional[float] = None
    fired: bool = False


class AlertRules:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.states = {key: _State() for key in ("cpu", "mem", "gpu", "temp", "disk")}
        self.last_alert: dict[str, float] = {}

    def feed(self, snapshot: dict, now: float) -> list[Alert]:
        alerts = []
        cpu = snapshot.get("cpu") or {}
        mem = snapshot.get("mem") or {}
        gpus = snapshot.get("gpu") or []
        sensors = snapshot.get("sensors") or []

        gpu_values = [g for g in gpus if g.get("util") is not None]
        gpu = max(gpu_values, key=lambda g: g["util"]) if gpu_values else None
        temp_values = [s for s in sensors if s.get("kind") == "temp" and s.get("value") is not None]
        temp = max(temp_values, key=lambda s: s["value"]) if temp_values else None

        measures = (
            ("cpu", cpu.get("percent"), self._cpu_alert(snapshot)),
            ("mem", mem.get("percent"), self._load_alert("mem", "内存", "内存持续吃紧")),
            ("gpu", gpu["util"] if gpu else None, self._load_alert("gpu", "显卡", "显卡一直在满负荷运转")),
            ("temp", temp["value"] if temp else None, self._temp_alert(temp)),
        )
        for key, value, alert in measures:
            if self._high_ready(key, value, now):
                alerts.append(alert)

        volumes = (snapshot.get("disk") or {}).get("volumes") or []
        free = [(v, max(0, v["total"] - v["used"]) / 2**30)
                for v in volumes
                if v.get("total") and v.get("used") is not None and not v.get("removable")]  # U 盘满了不提醒
        if free:
            volume, gb = min(free, key=lambda item: item[1])
            threshold = float(self.cfg["disk_free_gb"])
            state = self.states["disk"]
            # 空间不足的方向与占用率相反；超过阈值 5 GB 才算恢复。
            if all(remaining > threshold + 5 for _, remaining in free):
                state.fired = False
            elif gb < threshold and not state.fired and self._cooldown_ready("disk", now):
                state.fired = True
                self.last_alert["disk"] = now
                mount = str(volume.get("mount") or volume.get("device") or "磁盘").rstrip("\\/")
                alerts.append(Alert("disk", "磁盘空间快用完了", f"{mount} 只剩 {gb:.1f} GB 了，记得清理一下～"))
        return alerts

    def _high_ready(self, key: str, value, now: float) -> bool:
        state = self.states[key]
        if value is None:
            state.since = None
            return False
        threshold = float(self.cfg[key])
        if value < threshold - 5:
            state.since = None
            state.fired = False
            return False
        if value <= threshold:
            # 不再连续超标，但还没低到可重新提醒的恢复线。
            state.since = None
            return False
        if state.fired:
            return False
        if state.since is None:
            state.since = now
        if now - state.since < float(self.cfg["sustain_seconds"]):
            return False
        if not self._cooldown_ready(key, now):
            return False
        state.fired = True
        self.last_alert[key] = now
        return True

    def _cooldown_ready(self, key: str, now: float) -> bool:
        last = self.last_alert.get(key)
        return last is None or now - last >= float(self.cfg["cooldown_minutes"]) * 60

    def _load_alert(self, key: str, label: str, title: str) -> Alert:
        seconds = float(self.cfg["sustain_seconds"])
        threshold = float(self.cfg[key])
        return Alert(key, title, f"{label} 已经 {seconds:g} 秒超过 {threshold:g}% 了，要不要看看？")

    def _cpu_alert(self, snapshot: dict) -> Alert:
        alert = self._load_alert("cpu", "CPU", "CPU 一直在满负荷运转")
        processes = (snapshot.get("procs") or {}).get("cpu") or []
        if not processes:
            return alert
        busiest = max(processes, key=lambda p: p.get("cpu") or 0)
        name = busiest.get("name") or "未知进程"
        percent = float(busiest.get("cpu") or 0)
        message = alert.message.replace("要不要看看？", f"最忙的是 {name}（{percent:g}%）。要不要看看？")
        return Alert("cpu", alert.title, message)

    def _temp_alert(self, sensor: Optional[dict]) -> Alert:
        seconds = float(self.cfg["sustain_seconds"])
        threshold = float(self.cfg["temp"])
        name = (sensor or {}).get("name") or "硬件"
        return Alert("temp", "硬件温度有点高", f"{name} 已经 {seconds:g} 秒超过 {threshold:g} ℃ 了，留意一下散热哦～")
