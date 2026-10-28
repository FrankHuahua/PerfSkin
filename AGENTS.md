# AGENTS.md —— 给贡献者和 AI 编程助手的项目约定

PerfSkin 是一个 Windows 性能监控小工具：Python 后台采集数据，本机网页显示界面，图片皮肤决定整套配色。
动手前请读完本文件。人类贡献者同样适用。

## 架构

```
main.py                  入口
perfmon/app.py           主程序：配置、设置接口、托盘、窗口、皮肤切换、扩展加载
perfmon/collector.py     采集线程（psutil）；没人看界面时自动停掉进程统计
perfmon/gpu.py           显卡：NVML（NVIDIA）→ Windows PDH 计数器（任何显卡）
perfmon/sensors.py       传感器插槽；sensors_lhm.py 读 LibreHardwareMonitor
perfmon/skins.py         皮肤：图片取色、生成配色、皮肤库（内置 skins/ + 用户 user_skins/）
perfmon/procs.py         结束进程（系统关键进程一律拒绝）
perfmon/server.py        本机 HTTP + SSE（只监听 127.0.0.1，改动类接口要 X-Token）
perfmon/alerts.py        高负载提醒规则；ext_alerts.py 把它接到托盘通知
perfmon/ext_*.py         可选扩展：setup(app) 返回对象，可实现 start/stop/action/menu_items
web/                     界面：theme.css（共享基础）、app.*（主面板）、skins.js（皮肤与设置抽屉）、mini.*（迷你窗）
skins/                   内置皮肤（skin.json + 图片）
assets/                  图标、字体（Inter，OFL）
```

## 硬性规则

1. **省资源是第一原则。** 不加常驻动画（CSS 动画、`requestAnimationFrame` 循环、定时器重绘）；
   每次采样最多改一次 DOM、画一次图；页面隐藏时什么都不画；后台不轮询快于采样间隔。
   改动后用 `python main.py --demo` 打开界面，确认“自身”占用没有上升。
2. **颜色只从皮肤变量来。** 页面里不写死颜色，用 `var(--accent)`、`var(--surface)`、`var(--ink)` 等
   （见 `perfmon/skins.py` 的 `skin_css`）。新增颜色角色时，在 `build_palette` 里生成并保证 4.5:1 对比度。
3. **界面风格：深色、扁平。** 卡片只靠亮度台阶区分；不加描边、阴影（弹出层除外）、发光、渐变（图片遮罩除外）、
   毛玻璃；颜色只给数据和状态；数字用等宽数字（`.num`）右对齐。
4. **离线可用**：不引用 CDN 或网络资源，不引入前端框架。
5. **所有文件留在程序文件夹里**，不写系统盘的用户目录、`%APPDATA%`、`%TEMP%`。唯一例外是开机自启的注册表项。
6. **兼容 Python 3.9+**；Windows 专用代码用 `winsys.IS_WINDOWS` 判断，保证其他系统上 `--demo` 能跑。
7. **出错不能拖垮主程序**：扩展和线程自己 `try/except` 并记日志（`logging.getLogger(__name__)`）。
8. 注释、界面文字、日志用**中文**；变量名用英文。
9. 依赖优先用标准库和现有依赖（psutil、pystray、Pillow、nvidia-ml-py）。新依赖必须有 Windows x64 轮子或纯 Python。

## 自测

```
python main.py --demo --no-tray        # 演示数据，浏览器打开打印出来的地址
python main.py --check                 # 检测到的硬件和数据来源
python -m unittest discover tests      # 单元测试；新功能要带测试
```

## 提交

一个功能一个提交，说明里写清楚改了什么、怎么测的。改界面请附截图（1440 宽和 390 宽各一张）。
