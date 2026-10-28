"""结束进程（进程排行里的“结束”按钮）。

只结束当前用户自己的进程；系统关键进程、本程序自己和它的窗口一律拒绝。
同名进程（例如 chrome.exe ×32）作为一组一起结束，和排行里的分组一致。
"""
from __future__ import annotations

import logging
import os

import psutil

log = logging.getLogger(__name__)

# 结束这些会导致蓝屏、注销或桌面消失，界面上也不显示“结束”按钮
PROTECTED = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "memory compression",
    "secure system", "system idle process", "sihost.exe", "ctfmon.exe", "explorer.exe",
    "audiodg.exe", "spoolsv.exe", "wudfhost.exe", "conhost.exe", "taskhostw.exe",
    "msmpeng.exe", "searchindexer.exe", "runtimebroker.exe", "startmenuexperiencehost.exe",
    "textinputhost.exe", "shellexperiencehost.exe", "systemsettings.exe", "init", "systemd", "kthreadd",
}


def is_protected(name: str) -> bool:
    return (name or "").strip().lower() in PROTECTED


def _own_tree() -> set[int]:
    """本程序进程、它的父进程（启动它的 python / 批处理）和它打开的浏览器窗口进程。"""
    me = psutil.Process()
    pids = {me.pid}
    try:
        pids.update(p.pid for p in me.parents())
        pids.update(p.pid for p in me.children(recursive=True))
    except psutil.Error:
        pass
    return pids


def end_by_name(name: str, profile_marker: str | None = None, timeout: float = 3.0) -> dict:
    """结束所有同名进程。返回 {"ended": n, "denied": n, "skipped": n}。"""
    if not name or is_protected(name):
        raise PermissionError(f"{name} 是系统关键进程，不能结束")
    own = _own_tree()
    user = None
    try:
        user = psutil.Process().username()
    except psutil.Error:
        pass
    targets, skipped = [], 0
    for p in psutil.process_iter(["pid", "name", "username", "cmdline"]):
        if (p.info["name"] or "") != name:
            continue
        if p.info["pid"] in own or (profile_marker and profile_marker in " ".join(p.info["cmdline"] or [])):
            skipped += 1
            continue
        if user and p.info["username"] and p.info["username"] != user:
            skipped += 1                                   # 别的用户 / SYSTEM 的进程
            continue
        targets.append(p)
    denied = 0
    for p in targets:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            denied += 1
    gone, alive = psutil.wait_procs(targets, timeout=timeout)
    for p in alive:                                        # 不肯退出的再强制结束
        try:
            p.kill()
        except psutil.Error:
            denied += 1
    ended = len(targets) - denied
    log.info("结束进程 %s：%d 个，拒绝 %d 个，跳过 %d 个", name, ended, denied, skipped)
    return {"ended": max(0, ended), "denied": denied, "skipped": skipped, "pid": os.getpid()}
