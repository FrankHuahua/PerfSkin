"""本地 HTTP 服务（只监听 127.0.0.1）：提供界面静态文件和数据接口。

接口
  GET  /api/info              静态信息（主机名、CPU 型号、核心数、采样间隔……）
  GET  /api/snapshot          最新一次快照
  GET  /api/history           历史（紧凑格式：fields + rows）
  GET  /api/stream            SSE 实时推送：先发 event:hello（info + history），之后每次采样发 event:tick（snapshot）
  GET  /api/config            当前配置
  GET  /api/skins             皮肤列表 + 当前皮肤；GET /api/skin 当前皮肤；GET /skin.css 当前皮肤的 CSS 变量
  POST /api/skins             上传图片新建皮肤（请求体 = 图片字节，可带 X-Skin-Name 头），自动启用
  POST /api/skins/<id>/activate | update | delete   切换 / 编辑（JSON：name layout dim accent）/ 删除
  GET  /api/settings          界面“设置”页；POST /api/settings 修改（白名单键）
  POST /api/process/end       结束同名进程 {"name": "chrome.exe"}（系统关键进程拒绝）
  POST /api/action/<name>     操作（需要 X-Token 头）：open-window / open-config / open-log
页面（/、/mini.html ……）从 web/ 目录读取，/assets/ 对应 assets/ 目录。
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import posixpath
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import skins as skins_mod
from .collector import HISTORY_FIELDS
from .skins import MAX_UPLOAD

DEFAULT_SKIN = "maid"

log = logging.getLogger(__name__)
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("text/javascript", ".js")


def _round(o):
    """JSON 前把浮点数截短，SSE 每秒推送的数据量能小一半。"""
    if isinstance(o, float):
        return round(o, 2)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


def dumps(o) -> bytes:
    return json.dumps(_round(o), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Server:
    def __init__(self, app, host="127.0.0.1", port=47821):
        self.app = app
        self.collector = app.collector
        self.web_dir = os.path.join(app.res_dir, "web")
        self.assets_dir = os.path.join(app.res_dir, "assets")
        self.httpd = None
        for p in (port, 0):               # 端口被占用就让系统随便给一个
            try:
                self.httpd = ThreadingHTTPServer((host, p), self._handler())
                break
            except OSError:
                log.warning("端口 %s 被占用，换一个", p)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.clients = 0
        self._clients_lock = threading.Lock()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="http").start()
        log.info("服务已启动 %s", self.url)

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:  # noqa: BLE001
            pass

    def _handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "PerfMonitor"

            def log_message(self, fmt, *args):  # 不往控制台刷访问日志
                log.debug("%s " + fmt, self.address_string(), *args)

            # -------------------------------------------- 安全：只接受本机、正确 Host 的请求（防 DNS 重绑定）
            def _host_ok(self):
                host = (self.headers.get("Host") or "").split(":")[0].lower()
                return host in ("127.0.0.1", "localhost")

            def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                if not self._host_ok():
                    return self._send(403, b"forbidden", "text/plain")
                path = urllib.parse.urlparse(self.path).path
                c = srv.collector
                try:
                    if path == "/api/info":
                        return self._send(200, dumps(srv.info()))
                    if path == "/api/snapshot":
                        return self._send(200, dumps(c.latest() or {}))
                    if path == "/api/history":
                        return self._send(200, dumps(srv.history()))
                    if path == "/api/config":
                        return self._send(200, dumps(srv.app.cfg))
                    if path == "/api/stream":
                        return self._stream()
                    if path == "/api/settings":
                        return self._send(200, dumps(srv.app.settings()))
                    if path == "/api/skins":
                        return self._send(200, dumps(srv.skins_payload()))
                    if path == "/api/skin":
                        return self._send(200, dumps(srv.app.skin_public()))
                    if path == "/skin.css":
                        return self._send(200, srv.app.skin_css().encode("utf-8"), "text/css; charset=utf-8")
                    if path.startswith("/skins/"):
                        return self._skin_file(path)
                    if path.startswith("/api/"):
                        return self._send(404, b'{"error":"not found"}')
                    return self._static(path)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_POST(self):
                if not self._host_ok() or self.headers.get("X-Token") != srv.app.token:
                    return self._send(403, b'{"error":"forbidden"}')
                path = urllib.parse.urlparse(self.path).path
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n < 0 or n > MAX_UPLOAD:
                    self.close_connection = True
                    return self._send(413, dumps({"ok": False, "error": "图片太大了（上限 20 MB）"}))
                body = self.rfile.read(n) if n else b""
                if path.startswith("/api/skins"):
                    return self._skins_post(path, body)
                if path in ("/api/settings", "/api/process/end"):
                    try:
                        payload = json.loads(body or b"{}")
                        if path == "/api/settings":
                            return self._send(200, dumps({"ok": True, "settings": srv.app.update_settings(payload)}))
                        res = srv.app.end_process(str(payload.get("name", "")))
                        return self._send(200, dumps({"ok": True, **res}))
                    except PermissionError as e:
                        return self._send(403, dumps({"ok": False, "error": str(e)}))
                    except (ValueError, TypeError) as e:
                        return self._send(400, dumps({"ok": False, "error": str(e)}))
                if path.startswith("/api/action/"):
                    name = path.rsplit("/", 1)[-1]
                    try:
                        payload = json.loads(body or b"{}")
                    except ValueError:
                        payload = {}
                    ok = srv.app.action(name, payload)
                    return self._send(200 if ok else 400, dumps({"ok": bool(ok)}))
                return self._send(404, b'{"error":"not found"}')

            # -------------------------------------------- 皮肤
            def _skin_file(self, path):
                parts = urllib.parse.unquote(path).split("/")      # ['', 'skins', id, file]
                fp = srv.app.skins.file_path(parts[2], parts[3]) if len(parts) == 4 else None
                if not fp or not os.path.isfile(fp):
                    return self._send(404, b"not found", "text/plain")
                with open(fp, "rb") as f:
                    data = f.read()
                ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                return self._send(200, data, ctype, {"X-Content-Type-Options": "nosniff"})

            def _skins_post(self, path, body):
                app, parts = srv.app, path.strip("/").split("/")   # api/skins[/<id>/<op>]
                try:
                    if len(parts) == 2:                              # 上传新皮肤：请求体是图片原始字节
                        name = urllib.parse.unquote(self.headers.get("X-Skin-Name") or "")
                        skin = app.skins.create_from_image(body, name)
                        app.set_skin(skin["id"])
                        return self._send(200, dumps({"ok": True, "skin": app.skin_public(skin["id"])}))
                    if len(parts) == 4:
                        sid, op = parts[2], parts[3]
                        payload = json.loads(body or b"{}") if op == "update" else {}
                        if op == "activate":
                            if not app.skins.get(sid):
                                return self._send(404, dumps({"ok": False, "error": "没有这个皮肤"}))
                            app.set_skin(sid)
                            return self._send(200, dumps({"ok": True}))
                        if op == "update":
                            skin = app.skins.update(sid, payload)
                            app.set_skin(skin["id"])
                            return self._send(200, dumps({"ok": True, "skin": app.skin_public(skin["id"])}))
                        if op == "delete":
                            if not app.skins.delete(sid):
                                return self._send(400, dumps({"ok": False, "error": "内置皮肤不能删除"}))
                            if app.cfg.get("skin") == sid:
                                app.set_skin(DEFAULT_SKIN)
                            return self._send(200, dumps({"ok": True}))
                except KeyError:
                    return self._send(404, dumps({"ok": False, "error": "没有这个皮肤"}))
                except ValueError as e:
                    return self._send(400, dumps({"ok": False, "error": str(e)}))
                except Exception:  # noqa: BLE001
                    log.exception("皮肤操作失败")
                    return self._send(500, dumps({"ok": False, "error": "处理失败，详情见 perfmon.log"}))
                return self._send(404, dumps({"ok": False, "error": "not found"}))

            def _static(self, path):
                if path in ("", "/"):
                    path = "/index.html"
                base = srv.web_dir
                if path.startswith("/assets/"):
                    base, path = srv.assets_dir, path[len("/assets"):]
                rel = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
                if rel.startswith("..") or os.path.isabs(rel):
                    return self._send(404, b"not found", "text/plain")
                fp = os.path.join(base, *rel.split("/"))
                if not os.path.isfile(fp):
                    return self._send(404, b"not found", "text/plain")
                with open(fp, "rb") as f:
                    data = f.read()
                ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                cache = "max-age=86400" if base == srv.assets_dir else "no-cache"
                if rel == "index.html" or rel.endswith(".html"):
                    # 把操作令牌注入页面（只有本程序打开的页面能拿到）
                    data = data.replace(b"__PM_TOKEN__", srv.app.token.encode())
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", cache)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.close_connection = True
                with srv._clients_lock:
                    srv.clients += 1
                try:
                    hello = {"info": srv.info(), "history": srv.history(), "snapshot": srv.collector.latest(),
                             "skin": srv.app.skin_public()}
                    self.wfile.write(b"retry: 2000\nevent: hello\ndata: " + dumps(hello) + b"\n\n")
                    self.wfile.flush()
                    seq, last_ping = 0, time.monotonic()
                    skin_ver = srv.app.skin_version
                    while not srv.app.stopping:
                        if srv.app.skin_version != skin_ver:          # 换肤了：通知页面重新加载 /skin.css
                            skin_ver = srv.app.skin_version
                            self.wfile.write(b"event: skin\ndata: " + dumps(srv.app.skin_public()) + b"\n\n")
                            self.wfile.flush()
                        new_seq, snap = srv.collector.wait_next(seq, timeout=5.0)
                        if new_seq != seq and snap:
                            seq = new_seq
                            self.wfile.write(b"event: tick\ndata: " + dumps(snap) + b"\n\n")
                            self.wfile.flush()
                        elif time.monotonic() - last_ping > 15:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            last_ping = time.monotonic()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass
                finally:
                    with srv._clients_lock:
                        srv.clients -= 1

        return H

    def info(self):
        d = dict(self.collector.info)
        d["port"] = self.port
        d["features"] = sorted(self.app.features)
        return d

    def skins_payload(self):
        active = self.app.cfg.get("skin", DEFAULT_SKIN)
        return {"active": active, "skins": [skins_mod.public(s, "/skins/") for s in self.app.skins.list()]}

    def history(self):
        rows = self.collector.history()
        return {"fields": list(HISTORY_FIELDS), "rows": [[r.get(f) for f in HISTORY_FIELDS] for r in rows]}
