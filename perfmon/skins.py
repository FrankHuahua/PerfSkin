"""皮肤系统：从图片提取主色、生成整套界面配色、管理皮肤库。

一个皮肤 = 一个文件夹：
    skins/<id>/skin.json     名称、布局、配色（自动提取 + 用户覆盖）
    skins/<id>/image.webp    背景 / 立绘图片（可选）
    skins/<id>/avatar.webp   页脚小头像（可选，上传时自动从图片里裁）

内置皮肤放在程序资源目录的 skins/ 下（只读）；用户上传的皮肤放在程序目录的 skins/ 下。
两处同名时以用户目录为准。
"""
from __future__ import annotations

import colorsys
import io
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time

log = logging.getLogger(__name__)

MAX_UPLOAD = 20 * 1024 * 1024          # 上传大小上限
MAX_EDGE = 2400                        # 保存时图片最长边
LAYOUTS = ("figure", "backdrop", "none")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


# ============================================================== 颜色工具
def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"不是有效的颜色: {h!r}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in c[:3])


def _hls(c):
    return colorsys.rgb_to_hls(*(v / 255 for v in c[:3]))


def _rgb(h, l, s):
    return tuple(int(round(v * 255)) for v in colorsys.hls_to_rgb(h % 1.0, max(0, min(1, l)), max(0, min(1, s))))


def _luminance(c) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in c[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 1.0
    return min(d, 1 - d)


# ============================================================== 从图片取色
def extract_colors(img, n: int = 8) -> list[dict]:
    """返回图片里最有代表性的若干颜色：[{hex, weight, score}]，按“适合当强调色”的程度排序。

    做法：缩小 → 去掉透明像素 → 中位切分量化成 16 色 → 按 占比 × 鲜艳度 × 亮度合适度 打分。
    """
    from PIL import Image

    im = img.convert("RGBA")
    im.thumbnail((160, 160))
    raw = im.tobytes()                        # RGBA 连续字节；只取不透明的像素
    px = bytearray()
    for i in range(0, len(raw), 4):
        if raw[i + 3] >= 128:
            px += raw[i:i + 3]
    if len(px) < 48:                          # 几乎全透明：退回整图
        px = bytearray(im.convert("RGB").tobytes())
    flat = Image.frombytes("RGB", (len(px) // 3, 1), bytes(px))
    q = flat.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: 16 * 3]
    counts = sorted(q.getcolors(), reverse=True)
    total = sum(c for c, _ in counts) or 1
    out = []
    for count, idx in counts:
        rgb = tuple(pal[idx * 3: idx * 3 + 3])
        h, l, s = _hls(rgb)
        weight = count / total
        vivid = s * (1 - abs(2 * l - 1))          # 色度：太黑太白都不算鲜艳
        light_ok = 1 - min(1, abs(l - 0.55) / 0.45)
        score = (weight ** 0.45) * (0.15 + vivid) * (0.35 + light_ok)
        out.append({"hex": rgb_to_hex(rgb), "weight": round(weight, 4), "score": round(score, 4),
                    "h": h, "l": l, "s": s})
    # 去掉几乎一样的颜色
    uniq: list[dict] = []
    for c in sorted(out, key=lambda c: -c["score"]):
        if all(_hue_dist(c["h"], u["h"]) > 0.03 or abs(c["l"] - u["l"]) > 0.12 for u in uniq):
            uniq.append(c)
    for c in uniq:
        c.pop("h"), c.pop("l"), c.pop("s")
    return uniq[:n]


def dominant_hue(colors: list[dict]) -> float:
    """面积最大的那些颜色的平均色相（按占比和饱和度加权，用于背景底色）。"""
    x = y = 0.0
    for c in colors:
        h, l, s = _hls(hex_to_rgb(c["hex"]))
        w = c["weight"] * (0.2 + s)
        x += math.cos(h * 2 * math.pi) * w
        y += math.sin(h * 2 * math.pi) * w
    if abs(x) + abs(y) < 1e-6:
        return 0.06
    return (math.atan2(y, x) / (2 * math.pi)) % 1.0


def build_palette(accent_hex: str, colors: list[dict] | None = None, bg_hue: float | None = None) -> dict:
    """由一个强调色生成整套界面配色。

    设计取向（深色、扁平）：
    - 底色是“带温度的近黑”：色相取自图片主体，饱和度很低，所以暖图得到暖黑、冷图得到冷黑；
    - 卡片只靠亮度台阶区分（ground → surface → raised），不用描边和阴影；
    - 颜色只出现在数据上：强调色画主数据（CPU、下载），其余曲线用同色温的浅灰 / 中灰；
    - 文字三档灰全部保证 4.5:1 以上对比度。
    """
    ah, al, as_ = _hls(hex_to_rgb(accent_hex))
    neutral = as_ < 0.08                                   # 黑白图片：保持中性
    if not neutral:
        as_ = max(as_, 0.50)
    al = min(max(al, 0.58), 0.70)
    accent = _rgb(ah, al, as_)
    accent_l = _rgb(ah, min(al + 0.12, 0.84), as_ * 0.9)
    accent_d = _rgb(ah, max(al - 0.20, 0.28), as_)

    bh = ah if bg_hue is None else bg_hue
    t = 0.0 if neutral else 0.16                           # 底色的“温度”有多明显
    ground, surface, raised = _rgb(bh, 0.050, t), _rgb(bh, 0.090, t * 0.9), _rgb(bh, 0.140, t * 0.7)
    active, rule, control = _rgb(bh, 0.170, t * 0.6), _rgb(bh, 0.120, t * 0.8), _rgb(bh, 0.290, t * 0.5)
    ink = _rgb(bh, 0.960, t * 0.6)
    muted = _rgb(bh, 0.730, t * 0.35)
    faint = _rgb(bh, 0.615, t * 0.3)
    chart2 = _rgb(bh, 0.860, t * 0.4)
    chart3 = _rgb(bh, 0.500, t * 0.3)

    green = 0.22 <= ah <= 0.45 and not neutral
    pal = {
        "accent": rgb_to_hex(accent), "accent_l": rgb_to_hex(accent_l), "accent_d": rgb_to_hex(accent_d),
        "ground": rgb_to_hex(ground), "surface": rgb_to_hex(surface), "raised": rgb_to_hex(raised),
        "active": rgb_to_hex(active), "rule": rgb_to_hex(rule), "control": rgb_to_hex(control),
        "ink": rgb_to_hex(ink), "muted": rgb_to_hex(muted), "faint": rgb_to_hex(faint),
        "chart2": rgb_to_hex(chart2), "chart3": rgb_to_hex(chart3),
        "positive": rgb_to_hex(accent) if green else "#3fc07a",
        "negative": "#e5566f", "warning": "#e5a93b",
    }
    # 保险：强调色和三档文字在最亮的面板上都要够清楚
    if contrast(accent, raised) < 4.5:
        pal["accent"] = rgb_to_hex(_rgb(ah, 0.74, as_))
    for k in ("muted", "faint"):
        while contrast(hex_to_rgb(pal[k]), raised) < 4.6:
            h_, l_, s_ = _hls(hex_to_rgb(pal[k]))
            pal[k] = rgb_to_hex(_rgb(h_, l_ + 0.02, s_))
    return pal


def auto_accent(colors: list[dict]) -> str:
    return colors[0]["hex"] if colors else "#d97757"


# ============================================================== 皮肤 → CSS
def _ch(hex_):
    r, g, b = hex_to_rgb(hex_)
    return f"{r} {g} {b}"


def skin_css(skin: dict, url_prefix: str) -> str:
    """生成 :root 变量。页面里的颜色全部来自这些变量，所以换肤只需要换这一份 CSS。"""
    p = skin["palette"]
    dim = max(0.15, min(1.0, float(skin.get("dim", 0.85))))
    img = skin.get("image")
    layout = skin.get("layout", "figure") if img else "none"
    names = ("accent", "accent_l", "accent_d", "ground", "surface", "raised", "active", "rule", "control",
             "ink", "muted", "faint", "chart2", "chart3", "positive", "negative", "warning")
    lines = [f"--{k.replace('_', '-')}:{p[k]}" for k in names]
    lines += [f"--{k.replace('_', '-')}-rgb:{_ch(p[k])}" for k in ("accent", "ground", "surface", "ink")]
    lines += [
        f"--skin-dim:{dim:.2f}",
        f"--skin-image:{'url(' + json.dumps(url_prefix + img) + ')' if img else 'none'}",
        f"--skin-avatar:{'url(' + json.dumps(url_prefix + skin['avatar']) + ')' if skin.get('avatar') else 'none'}",
    ]
    name = str(skin.get("name", "")).replace("*/", "")
    return (f"/* 皮肤：{name}（自动生成，请勿手改） */\n"
            f":root{{{';'.join(lines)}}}\n"
            f"html{{--layout:{layout}}}\n")


# ============================================================== 皮肤库
class SkinStore:
    def __init__(self, builtin_dir: str, user_dir: str):
        self.builtin_dir = builtin_dir
        self.user_dir = user_dir
        self.version = 1                 # 每次换肤/编辑 +1，页面据此刷新
        self._lock = threading.RLock()
        os.makedirs(user_dir, exist_ok=True)

    # ---------------------------------------------------- 读取
    def _dir_of(self, sid: str) -> str | None:
        if not _ID_RE.match(sid or ""):
            return None
        for base in (self.user_dir, self.builtin_dir):
            d = os.path.join(base, sid)
            if os.path.isfile(os.path.join(d, "skin.json")):
                return d
        return None

    def is_builtin(self, sid: str) -> bool:
        d = self._dir_of(sid)
        return bool(d) and os.path.dirname(d) == os.path.normpath(self.builtin_dir)

    def get(self, sid: str) -> dict | None:
        d = self._dir_of(sid)
        if not d:
            return None
        try:
            with open(os.path.join(d, "skin.json"), encoding="utf-8") as f:
                skin = json.load(f)
        except (OSError, ValueError):
            log.exception("皮肤 %s 读取失败", sid)
            return None
        skin["id"] = sid
        skin["builtin"] = os.path.normpath(os.path.dirname(d)) == os.path.normpath(self.builtin_dir)
        for k in ("image", "avatar"):
            if skin.get(k) and not os.path.isfile(os.path.join(d, skin[k])):
                skin[k] = None
        skin.setdefault("layout", "figure" if skin.get("image") else "none")
        skin.setdefault("dim", 0.85)
        skin.setdefault("colors", [])
        if not skin.get("palette") or "ground" not in skin["palette"]:
            skin["palette"] = build_palette(skin.get("accent") or auto_accent(skin["colors"]), skin["colors"],
                                            skin.get("bg_hue"))
        return skin

    def list(self) -> list[dict]:
        ids = set()
        for base in (self.builtin_dir, self.user_dir):
            if os.path.isdir(base):
                ids.update(n for n in os.listdir(base) if _ID_RE.match(n))
        skins = [s for s in (self.get(i) for i in sorted(ids)) if s]
        skins.sort(key=lambda s: (not s["builtin"], s.get("order", 50), s.get("created", 0)))
        return skins

    def file_path(self, sid: str, name: str) -> str | None:
        """皮肤里的图片文件（只允许 skin.json 里登记的那几个文件名）。"""
        skin = self.get(sid)
        if not skin or name not in {skin.get("image"), skin.get("avatar"), skin.get("thumb")} - {None}:
            return None
        return os.path.join(self._dir_of(sid), name)

    # ---------------------------------------------------- 新建（上传）
    def create_from_image(self, data: bytes, name: str = "") -> dict:
        from PIL import Image, ImageOps

        if not data:
            raise ValueError("没有收到图片")
        if len(data) > MAX_UPLOAD:
            raise ValueError("图片太大了（上限 20 MB）")
        Image.MAX_IMAGE_PIXELS = 60_000_000
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception as e:  # noqa: BLE001  （包括解压炸弹）
            raise ValueError("无法识别这张图片，请换 PNG / JPG / WebP") from e
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        im = ImageOps.exif_transpose(im)
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if has_alpha else "RGB")
        im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)

        transparent = False
        if has_alpha:
            alpha = im.getchannel("A")
            hist = alpha.histogram()
            transparent = sum(hist[:16]) / max(1, im.width * im.height) > 0.08
        layout = "figure" if (transparent or im.height / im.width > 1.15) else "backdrop"

        colors = extract_colors(im)
        accent = auto_accent(colors)
        sid = time.strftime("u%y%m%d-") + secrets.token_hex(3)
        d = os.path.join(self.user_dir, sid)
        os.makedirs(d)
        try:
            im.save(os.path.join(d, "image.webp"), "WEBP", quality=88, method=4)   # 重新编码 = 丢弃 EXIF 等元数据
            self._make_avatar(im).save(os.path.join(d, "avatar.webp"), "WEBP", quality=90)
            skin = {
                "name": (name or "我的皮肤").strip()[:40] or "我的皮肤",
                "image": "image.webp", "avatar": "avatar.webp",
                "layout": layout, "dim": 0.85, "colors": colors, "accent": None,
                "bg_hue": round(dominant_hue(colors), 4),
                "created": int(time.time()),
            }
            skin["palette"] = build_palette(accent, colors, skin["bg_hue"])
            self._write(d, skin)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        log.info("新皮肤 %s：布局 %s，强调色 %s", sid, layout, skin["palette"]["accent"])
        del im
        import gc
        gc.collect()                            # 大图处理完马上还内存，常驻占用回到平时水平
        return self.get(sid)

    @staticmethod
    def _make_avatar(im):
        """头像：取图片主体（非透明区域）上部居中的正方形。"""
        from PIL import Image
        box = im.getchannel("A").getbbox() if im.mode == "RGBA" else None
        x0, y0, x1, y1 = box or (0, 0, im.width, im.height)
        w, h = x1 - x0, y1 - y0
        side = min(w, h, int(max(w, h) * 0.55)) if h > w * 1.15 else min(w, h)
        cx = x0 + w // 2
        top = y0 + (int(h * 0.04) if h > w * 1.15 else (h - side) // 2)
        crop = im.crop((cx - side // 2, top, cx - side // 2 + side, top + side))
        bg = Image.new("RGB", crop.size, (20, 18, 16))
        if crop.mode == "RGBA":
            bg.paste(crop, mask=crop.getchannel("A"))
        else:
            bg.paste(crop)
        return bg.resize((240, 240), Image.LANCZOS)

    # ---------------------------------------------------- 编辑 / 删除
    def update(self, sid: str, changes: dict) -> dict:
        with self._lock:
            skin = self.get(sid)
            if not skin:
                raise KeyError(sid)
            if skin["builtin"]:
                skin = self._fork_builtin(sid)
                sid = skin["id"]
            if "name" in changes:
                skin["name"] = str(changes["name"]).strip()[:40] or skin["name"]
            if "layout" in changes:
                if changes["layout"] not in LAYOUTS:
                    raise ValueError("布局只能是 figure / backdrop / none")
                skin["layout"] = changes["layout"]
            if "dim" in changes:
                skin["dim"] = round(max(0.15, min(1.0, float(changes["dim"]))), 2)
            if "accent" in changes:
                a = changes["accent"]
                skin["accent"] = rgb_to_hex(hex_to_rgb(a)) if a else None
            skin["palette"] = build_palette(skin.get("accent") or auto_accent(skin.get("colors", [])),
                                            skin.get("colors"), skin.get("bg_hue"))
            self._write(self._dir_of(sid), skin)
            self.version += 1
            return self.get(sid)

    def _fork_builtin(self, sid: str) -> dict:
        """内置皮肤只读：第一次编辑时在用户目录复制一份同名皮肤（覆盖内置的那份）。"""
        src = os.path.join(self.builtin_dir, sid)
        dst = os.path.join(self.user_dir, sid)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        skin = self.get(sid)
        skin["builtin"] = False
        return skin

    def delete(self, sid: str) -> bool:
        with self._lock:
            d = os.path.join(self.user_dir, sid)
            if not _ID_RE.match(sid) or not os.path.isdir(d):
                return False
            shutil.rmtree(d)
            self.version += 1
            return True

    @staticmethod
    def _write(d: str, skin: dict):
        data = {k: v for k, v in skin.items() if k not in ("id", "builtin")}
        tmp = os.path.join(d, "skin.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(d, "skin.json"))


def public(skin: dict, url_prefix: str) -> dict:
    """给前端的皮肤信息（带图片地址）。"""
    out = {k: skin.get(k) for k in ("id", "name", "builtin", "layout", "dim", "accent", "palette", "colors")}
    for k in ("image", "avatar"):
        out[k] = url_prefix + skin["id"] + "/" + skin[k] if skin.get(k) else None
    return out
