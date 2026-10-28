"""迷你悬浮窗扩展：提供托盘入口，并在 Windows 上设置置顶。"""
from __future__ import annotations

import logging
import threading
import time

from . import winsys

log = logging.getLogger(__name__)
TITLE = "迷你监控 · PerfSkin"


class MiniExtension:
    def __init__(self, app):
        self.app = app

    def menu_items(self):
        return [("迷你悬浮窗", self.open)]

    def action(self, name, payload):
        if name != "open-mini":
            return False
        self.open()
        return True

    def open(self):
        try:
            self.app.open_window("mini.html", size=tuple(self.app.cfg["mini"]["size"]), title=TITLE)
            if winsys.IS_WINDOWS:
                threading.Thread(target=self._place_window, daemon=True, name="mini-window").start()
        except Exception:  # noqa: BLE001
            log.exception("打开迷你悬浮窗失败")

    def _place_window(self):
        try:
            deadline = time.monotonic() + 6.0
            while not self.app.stopping and time.monotonic() < deadline:
                hwnd = winsys.find_window(TITLE)
                if hwnd:
                    if self.app.cfg["mini"]["topmost"]:
                        winsys.set_topmost(hwnd)
                    return
                time.sleep(0.25)
        except Exception:  # noqa: BLE001
            log.exception("设置迷你悬浮窗置顶失败")


def setup(app):
    app.features.add("mini")
    return MiniExtension(app)
