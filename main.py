"""PerfSkin —— 可以换皮肤的 Windows 性能监控。

双击 run.bat 启动，或：python main.py [--demo] [--no-tray] [--check]
"""
import sys

from perfmon.app import main

if __name__ == "__main__":
    sys.exit(main())
