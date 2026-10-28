"""皮肤系统：取色、配色对比度、皮肤库增删改、HTTP 接口。"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw  # noqa: E402

from perfmon import skins  # noqa: E402


def png(color=(40, 120, 220), size=(400, 300), alpha=False, accent=None) -> bytes:
    im = Image.new("RGBA" if alpha else "RGB", size, (0, 0, 0, 0) if alpha else color)
    d = ImageDraw.Draw(im)
    if alpha:
        d.ellipse((100, 20, 300, 290), fill=color + (255,))
    if accent:
        d.rectangle((0, 0, size[0] // 3, size[1] // 3), fill=accent)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class PaletteTest(unittest.TestCase):
    def test_vivid_colour_wins_accent(self):
        im = Image.open(io.BytesIO(png((30, 30, 34), accent=(230, 80, 40))))
        colors = skins.extract_colors(im)
        h, l, s = skins._hls(skins.hex_to_rgb(colors[0]["hex"]))
        self.assertGreater(s, 0.5, "鲜艳的橙色应该排在灰色前面")

    def test_palette_is_readable_for_any_accent(self):
        for accent in ("#d97757", "#2050ff", "#ffff00", "#101010", "#ffffff", "#7f7f7f", "#00ff66"):
            p = skins.build_palette(accent, [], 0.5)
            for k in ("accent", "ink", "muted", "faint"):
                self.assertGreaterEqual(skins.contrast(skins.hex_to_rgb(p[k]), skins.hex_to_rgb(p["raised"])), 4.5,
                                        f"{accent}: {k} 在最亮的面板上对比度不足")

    def test_ground_takes_image_temperature(self):
        warm = skins.build_palette("#d97757", [], 0.05)["ground"]
        cool = skins.build_palette("#d97757", [], 0.60)["ground"]
        wr, wg, wb = skins.hex_to_rgb(warm)
        cr, cg, cb = skins.hex_to_rgb(cool)
        self.assertGreater(wr, wb)
        self.assertGreater(cb, cr)

    def test_css_has_every_token(self):
        skin = {"name": "t */ x", "palette": skins.build_palette("#3366ff"), "image": "image.webp", "layout": "figure"}
        css = skins.skin_css(skin, "/skins/t/")
        for k in ("--accent:", "--ground:", "--surface:", "--ink:", "--skin-image:url(\"/skins/t/image.webp\")"):
            self.assertIn(k, css)
        self.assertNotIn("*/ x", css.split("\n", 1)[1])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = skins.SkinStore(os.path.join(ROOT, "skins"), self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_builtins_listed(self):
        ids = [s["id"] for s in self.store.list()]
        self.assertIn("maid", ids)
        self.assertTrue(self.store.get("maid")["builtin"])

    def test_upload_layouts(self):
        wide = self.store.create_from_image(png(size=(1600, 900)), "风景")
        self.assertEqual(wide["layout"], "backdrop")
        figure = self.store.create_from_image(png(alpha=True, size=(500, 700)), "立绘")
        self.assertEqual(figure["layout"], "figure")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, figure["id"], "avatar.webp")))

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            self.store.create_from_image(b"not an image")
        with self.assertRaises(ValueError):
            self.store.create_from_image(b"")
        self.assertEqual(os.listdir(self.tmp), [])

    def test_update_and_delete(self):
        s = self.store.create_from_image(png(), "a")
        s2 = self.store.update(s["id"], {"accent": "#ff0000", "dim": 0.5, "name": "  新名字 "})
        self.assertEqual(s2["name"], "新名字")
        self.assertEqual(s2["dim"], 0.5)
        self.assertNotEqual(s2["palette"]["accent"], s["palette"]["accent"])
        with self.assertRaises(ValueError):
            self.store.update(s["id"], {"layout": "sideways"})
        self.assertTrue(self.store.delete(s["id"]))
        self.assertIsNone(self.store.get(s["id"]))

    def test_builtin_is_forked_not_modified(self):
        path = os.path.join(ROOT, "skins", "maid", "skin.json")
        with open(path, encoding="utf-8") as f:
            before = f.read()
        s = self.store.update("maid", {"dim": 0.4})
        self.assertFalse(s["builtin"])
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        self.assertFalse(self.store.delete("../maid"))

    def test_file_path_only_registered_files(self):
        self.assertIsNotNone(self.store.file_path("maid", "maid.webp"))
        self.assertIsNone(self.store.file_path("maid", "skin.json"))
        self.assertIsNone(self.store.file_path("../perfmon", "app.py"))


class ApiTest(unittest.TestCase):
    """起一个真实的服务（演示数据），走一遍上传 → 换肤 → 设置。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.patches = [mock.patch("perfmon.winsys.app_dir", return_value=cls.tmp),
                       mock.patch("perfmon.winsys.resource_dir", return_value=ROOT)]
        for p in cls.patches:
            p.start()
        from perfmon.app import App
        args = SimpleNamespace(demo=True, no_tray=True, no_window=True, background=True, port=0)
        cls.app = App(args)
        cls.app.server.start()
        cls.app.collector.start()
        cls.base = cls.app.server.url

    @classmethod
    def tearDownClass(cls):
        cls.app.stopping = True
        cls.app.collector.stop()
        cls.app.server.stop()
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, path, data=None, headers=None, token=True):
        h = {"X-Token": self.app.token} if token else {}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path.lstrip("/"), data=data, headers=h,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_upload_switch_and_css(self):
        code, body = self.call("/api/skins", png(accent=(20, 200, 90)), {"Content-Type": "image/png",
                                                                          "X-Skin-Name": "%E7%BB%BF"})
        self.assertEqual(code, 200, body)
        skin = json.loads(body)["skin"]
        self.assertEqual(skin["name"], "绿")
        self.assertEqual(self.app.cfg["skin"], skin["id"])
        code, css = self.call("/skin.css")
        self.assertIn(skin["palette"]["accent"], css.decode())
        code, img = self.call(skin["image"])
        self.assertEqual(code, 200)
        self.assertEqual(self.call("/api/skins/maid/activate", b"")[0], 200)
        self.assertEqual(self.app.cfg["skin"], "maid")

    def test_token_required(self):
        self.assertEqual(self.call("/api/skins", png(), token=False)[0], 403)
        self.assertEqual(self.call("/api/settings", b"{}", token=False)[0], 403)

    def test_settings_whitelist_and_ranges(self):
        code, body = self.call("/api/settings", json.dumps({"interval": 2, "alerts": {"cpu": 70}, "evil": 1}).encode())
        self.assertEqual(code, 200)
        self.assertEqual(self.app.collector.interval, 2.0)
        self.assertEqual(self.app.cfg["alerts"]["cpu"], 70)
        self.assertNotIn("evil", self.app.cfg)
        self.assertEqual(self.call("/api/settings", json.dumps({"interval": 0.01}).encode())[0], 400)
        self.assertEqual(self.call("/api/settings", json.dumps({"alerts": {"cpu": 500}}).encode())[0], 400)
        self.call("/api/settings", json.dumps({"interval": 1}).encode())

    def test_protected_process_refused(self):
        code, _ = self.call("/api/process/end", json.dumps({"name": "csrss.exe"}).encode())
        self.assertEqual(code, 403)

    def test_skin_event_on_stream(self):
        got = []

        def read():
            req = urllib.request.Request(self.base + "api/stream")
            with urllib.request.urlopen(req, timeout=10) as r:
                for line in r:
                    if line.startswith(b"event: skin"):
                        got.append(True)
                        return
        t = threading.Thread(target=read, daemon=True)
        t.start()
        time.sleep(1.0)
        self.call("/api/skins/minimal/activate", b"")
        t.join(5)
        self.assertTrue(got, "换肤后应该推送 skin 事件")
        self.call("/api/skins/maid/activate", b"")


if __name__ == "__main__":
    unittest.main()
