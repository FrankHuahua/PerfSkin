"""通过 LibreHardwareMonitor 的本地网页服务读取传感器。"""
from __future__ import annotations

import json
import logging
import math
import re
import urllib.request

from .sensors import SensorProvider, reading

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8085/data.json"
_VALUE = re.compile(r"^\s*([+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?|[.,]\d+))\s*(\S*)\s*$")
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_KINDS = {"temperature": ("temp", "℃"), "fan": ("fan", "RPM"), "power": ("power", "W")}
_UNITS = {"°c": ("temp", "℃"), "℃": ("temp", "℃"), "rpm": ("fan", "RPM"), "w": ("power", "W")}
_CATEGORIES = {"temperatures", "fans", "powers", "loads", "clocks", "voltages", "controls", "data"}


def _group(text: str) -> str:
    value = text.casefold()
    if any(word in value for word in ("gpu", "nvidia", "amd radeon", "intel arc")):
        return "gpu"
    if "cpu" in value:
        return "cpu"
    if any(word in value for word in ("nvme", "ssd", "hdd", "storage")):
        return "disk"
    if any(word in value for word in ("主板", "motherboard", "mainboard", "lpc", "superio")):
        return "board"
    if re.search(r"\b(?:it|nct)\d{4}", value):
        return "board"
    return "other"


def _icon_group(node: dict) -> str:
    icon = str(node.get("ImageURL", "")).rsplit("/", 1)[-1].casefold()
    return {
        "cpu.png": "cpu", "nvidia.png": "gpu", "ati.png": "gpu", "intel.png": "gpu",
        "hdd.png": "disk", "mainboard.png": "board", "chip.png": "board",
    }.get(icon, "other")


def _hardware(ancestors: tuple[dict, ...]) -> tuple[str, str]:
    for node in reversed(ancestors):
        name = str(node.get("Text") or "")
        if node.get("HardwareId") or _icon_group(node) != "other" or _group(name) != "other":
            return name, _group(name) if _group(name) != "other" else _icon_group(node)
    for node in reversed(ancestors):
        name = str(node.get("Text") or "")
        if name and name.casefold() not in _CATEGORIES and name != "Sensor":
            return name, "other"
    return "", "other"


def _parse(root: dict) -> list[dict]:
    out = []
    stack = [(root, ())]
    while stack:
        node, ancestors = stack.pop()
        if not isinstance(node, dict):
            continue
        children = node.get("Children")
        if isinstance(children, list) and children:
            stack.extend((child, ancestors + (node,)) for child in reversed(children))
            continue
        match = _VALUE.fullmatch(str(node.get("Value") or ""))
        if not match:
            continue
        if node.get("Type") is not None:
            kind = _KINDS.get(str(node["Type"]).casefold())
        else:
            kind = _UNITS.get(match.group(2).casefold())
        if kind is None:
            continue
        raw = match.group(1)
        value = float(raw.replace(",", "" if _THOUSANDS.fullmatch(raw) else "."))
        if not math.isfinite(value) or (kind[0] == "fan" and value == 0):
            continue
        hw, parent_group = _hardware(ancestors)
        group = _group(str(node.get("SensorId") or ""))
        if group == "other":
            group = parent_group
        out.append(reading(str(node.get("Text") or ""), kind[0], value, kind[1], group, hw))
    return out


class LhmSensors(SensorProvider):
    """LHM 不可用时构造失败；运行时短暂故障沿用最近一次读数。"""

    name = "lhm"
    wants_config = True

    def __init__(self, cfg):
        self.url = (cfg.get("lhm") or {}).get("url") or DEFAULT_URL
        self._last = self._fetch()
        self._failures = 0

    def _fetch(self) -> list[dict]:
        with urllib.request.urlopen(self.url, timeout=0.8) as response:
            return _parse(json.load(response))

    def read(self) -> list[dict]:
        try:
            result = self._fetch()
        except Exception as exc:  # noqa: BLE001
            self._failures += 1
            if self._failures in (1, 5):
                log.warning("LibreHardwareMonitor 读取失败（连续 %d 次）：%s", self._failures, exc)
            if self._failures >= 5:
                self._last = []
            return list(self._last)
        self._last = result
        self._failures = 0
        return list(result)
