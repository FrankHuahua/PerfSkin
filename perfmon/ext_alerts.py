"""从采样器接收快照，并通过托盘发送高负载提醒。"""
from __future__ import annotations

import logging
import threading
import time

from .alerts import AlertRules

log = logging.getLogger(__name__)


class AlertsExtension:
    def __init__(self, app):
        self.app = app
        self.rules = AlertRules(app.cfg["alerts"])
        self._stop = threading.Event()
        self._thread = None

    @property
    def enabled(self):
        return bool(self.app.cfg["alerts"]["enabled"])

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="alerts")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def menu_items(self):
        return [("高负载提醒", self.toggle, lambda: self.enabled)]

    def toggle(self):
        self.app.cfg["alerts"]["enabled"] = not self.enabled
        # 重新开启时从当前样本计时，不沿用关闭前的负载状态。
        self.rules = AlertRules(self.app.cfg["alerts"])
        from .app import save_json
        save_json(self.app.config_path, self.app.cfg)
        tray = self.app.tray
        if tray is not None:
            try:
                tray.update_menu()
            except Exception:  # noqa: BLE001
                log.exception("更新高负载提醒菜单失败")

    def _run(self):
        seq = 0
        while not self._stop.is_set() and not self.app.stopping:
            try:
                next_seq, snapshot = self.app.collector.wait_next(seq, timeout=5)
                if self._stop.is_set() or self.app.stopping:
                    break
                if next_seq == seq:
                    continue
                seq = next_seq
                if not snapshot or not self.enabled or self.app.tray is None:
                    continue
                for alert in self.rules.feed(snapshot, time.monotonic()):
                    if self._stop.is_set() or not self.enabled:
                        break
                    self.app.tray.notify(alert.message, alert.title)
            except Exception:  # noqa: BLE001
                log.exception("高负载提醒线程出错")
                self._stop.wait(5)


def setup(app):
    return AlertsExtension(app)
