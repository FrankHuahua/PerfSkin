"""Windows 相关的小工具：程序目录、单实例、开机自启、找浏览器、激活已有窗口、结束旧实例。
非 Windows 平台上这些函数都会安全地什么也不做（方便在别的系统上调试 --demo）。"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys

log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"
APP_NAME = "PerfSkin"
WINDOW_TITLE = "性能监控 · PerfSkin"      # 与 web/index.html 的 <title> 保持一致

if IS_WINDOWS:
    import ctypes
    import winreg
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def app_dir() -> str:
    """程序所在目录：配置、日志、浏览器配置、你上传的皮肤都放这里。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir() -> str:
    """web/ 和 assets/ 所在目录（打包后在 _internal 里）。"""
    return getattr(sys, "_MEIPASS", None) or app_dir()


# ---------------------------------------------------------------- 单实例
_mutex = None


def single_instance() -> bool:
    global _mutex
    if not IS_WINDOWS:
        return True
    h = kernel32.CreateMutexW(None, False, "Local\\" + APP_NAME + "_SingleInstance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(h)
        return False
    _mutex = h
    return True


# ---------------------------------------------------------------- 开机自启
def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        w = exe[:-10] + "pythonw.exe"
        if os.path.exists(w):
            exe = w
    return f'"{exe}" "{os.path.join(app_dir(), "main.py")}" --background'


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enable: bool):
    if not IS_WINDOWS:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


# ---------------------------------------------------------------- 系统信息
def cpu_name() -> str:
    if IS_WINDOWS:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    else:
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or "CPU"


def os_name() -> str:
    if IS_WINDOWS:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
                build = int(winreg.QueryValueEx(k, "CurrentBuildNumber")[0])
                try:
                    disp = winreg.QueryValueEx(k, "DisplayVersion")[0]
                except OSError:
                    disp = ""
            return f"Windows {'11' if build >= 22000 else '10'} {disp}".strip()
        except OSError:
            pass
    return f"{platform.system()} {platform.release()}"


def video_adapters() -> list[dict]:
    """从注册表读显卡名称和显存（给不支持 NVML 的显卡用）。"""
    out = []
    if not IS_WINDOWS:
        return out
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as k:
                        name = winreg.QueryValueEx(k, "DriverDesc")[0]
                        mem = 0
                        for v in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                raw = winreg.QueryValueEx(k, v)[0]
                                mem = int.from_bytes(raw, "little") if isinstance(raw, bytes) else int(raw)
                                break
                            except OSError:
                                continue
                        if "Basic" in name or "Remote" in name or "Virtual" in name:
                            continue
                        out.append({"name": name, "vram": mem})
                except OSError:
                    continue
    except OSError:
        pass
    return out


# ---------------------------------------------------------------- 浏览器窗口
def find_browser() -> str | None:
    if not IS_WINDOWS:
        for c in ("/usr/bin/microsoft-edge", "/usr/bin/google-chrome", "/usr/bin/chromium"):
            if os.path.exists(c):
                return c
        return None
    env = os.environ
    cands = [
        os.path.join(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(env.get("ProgramFiles", r"C:\Program Files"), r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(env.get("ProgramFiles", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(env.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    ]
    return next((c for c in cands if c and os.path.exists(c)), None)


def find_window(title_part: str):
    """返回第一个标题包含 title_part 的可见顶层窗口句柄，找不到返回 None。"""
    if not IS_WINDOWS:
        return None
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title_part in buf.value:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def set_topmost(hwnd, on=True):
    """把窗口设为（或取消）总在最前。"""
    if IS_WINDOWS and hwnd:
        # HWND_TOPMOST = -1, HWND_NOTOPMOST = -2；SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE
        user32.SetWindowPos(hwnd, -1 if on else -2, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)


def focus_window(title_part: str = WINDOW_TITLE) -> bool:
    """如果已有标题包含 title_part 的窗口，把它恢复并放到最前面。"""
    hwnd = find_window(title_part)
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # 前台锁：先模拟一次 Alt，Windows 才允许后台进程把窗口提到前面
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    return True


def open_app_window(url: str, profile_dir: str, size=(1360, 880), title_part=WINDOW_TITLE) -> bool:
    """用 Edge/Chrome 的 --app 模式打开一个无地址栏的窗口；已经打开就激活它。"""
    if focus_window(title_part):
        return True
    b = find_browser()
    if not b:
        import webbrowser
        webbrowser.open(url)
        return False
    args = [b, f"--app={url}", f"--user-data-dir={profile_dir}", f"--window-size={size[0]},{size[1]}",
            "--no-first-run", "--no-default-browser-check", "--disable-session-crashed-bubble",
            # 省资源：这个窗口只显示一个本地页面，关掉浏览器的后台服务
            "--disable-extensions", "--disable-background-networking", "--disable-sync",
            "--disable-component-update", "--disable-default-apps", "--no-pings", "--disable-breakpad",
            "--disable-features=Translate,msEdgeShopping,EdgeCollections,msImplicitSignin,"
            "AutofillServerCommunication,MediaRouter,OptimizationHints",
            "--renderer-process-limit=2"]
    try:
        subprocess.Popen(args, close_fds=True, creationflags=NO_WINDOW if IS_WINDOWS else 0)
        return True
    except OSError:
        log.exception("启动浏览器失败")
        return False


def close_app_windows(profile_dir: str):
    """退出时关掉本程序打开的窗口（只针对专用的浏览器配置目录，不影响平时用的浏览器）。"""
    if not IS_WINDOWS:
        return
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
          "$_.CommandLine.Contains($env:PM_PROFILE) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
          "-ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=20,
                       creationflags=NO_WINDOW, env=dict(os.environ, PM_PROFILE=profile_dir),
                       capture_output=True)
    except Exception:
        log.exception("关闭窗口失败")


def open_path(path: str):
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606
    else:
        log.info("open %s", path)


_STOP_PS = r"""
$dir = $env:PM_DIR; $me = [int]$env:PM_PID
$all = @(Get-CimInstance Win32_Process)
$myParent = ($all | Where-Object { $_.ProcessId -eq $me }).ParentProcessId
function InDir($p) { $p -and $p.ExecutablePath -and $p.ExecutablePath.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) }
$n = 0
foreach ($p in $all) {
  if ($p.ProcessId -eq $me -or $p.ProcessId -eq $myParent) { continue }
  $isApp = ($p.Name -like 'PerfSkin*') -and (InDir $p)
  $isPy = ($p.Name -match '^pythonw?\.exe$') -and $p.CommandLine -and $p.CommandLine.Contains('main.py')
  if ($isPy) { $isPy = $p.CommandLine.Contains($dir) -or (InDir $p) }
  if ($isApp -or $isPy) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }
}
$n
"""


def stop_other_instances() -> int:
    """结束本程序的其他副本（run.bat 用它来“重启成最新版本”）。"""
    if not IS_WINDOWS:
        return 0
    env = dict(os.environ, PM_DIR=app_dir(), PM_PID=str(os.getpid()))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _STOP_PS],
                           capture_output=True, timeout=30, creationflags=NO_WINDOW, env=env)
        out = (r.stdout or b"").decode("utf-8", "ignore").strip().splitlines()
        return int(out[-1]) if out and out[-1].isdigit() else 0
    except Exception:
        log.exception("结束旧实例失败")
        return 0
