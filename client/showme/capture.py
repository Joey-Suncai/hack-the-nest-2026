"""截屏、缩放、编码。"""

import base64
import io
import os
import subprocess
import tempfile

import mss
import numpy as np
from PIL import Image

from showme.config import JPEG_QUALITY, MAX_SIDE


# 截图时要排除的窗口（CGWindowID）：ShowMe 的高亮层（框、气泡、字幕、Listening 标签都画在它上面）。
# 只靠「清空内容再重绘」不可靠：重绘要等 Core Animation 提交、WindowServer 合成，偶尔截到上一步的框和气泡
# （日志 06_next_screen.jpg）。直接让 WindowServer 合成时不带这些窗口，就跟重绘时机无关了。
EXCLUDE_WINDOW_IDS: set[int] = set()


def exclude_window(window_id: int) -> None:
    EXCLUDE_WINDOW_IDS.add(int(window_id))


def _grab_excluding(exclude: set[int]) -> Image.Image | None:
    """主显示器上除 exclude 以外的所有窗口合成一张图（和 mss 一样是逻辑尺寸）。失败返回 None。"""
    try:
        import Quartz as Q

        ids = Q.CGWindowListCreate(Q.kCGWindowListOptionOnScreenOnly, Q.kCGNullWindowID)
        if not ids:
            return None
        keep = [int(i) for i in ids if int(i) not in exclude]  # 顺序是从前到后，保持不变
        bounds = Q.CGDisplayBounds(Q.CGMainDisplayID())
        opts = Q.kCGWindowImageBoundsIgnoreFraming | Q.kCGWindowImageShouldBeOpaque | Q.kCGWindowImageNominalResolution
        img = Q.CGWindowListCreateImageFromArray(bounds, keep, opts)  # 选项和 mss 一样：逻辑尺寸、不透明
        if img is None:
            return None
        w, h = Q.CGImageGetWidth(img), Q.CGImageGetHeight(img)
        if Q.CGImageGetBitsPerPixel(img) != 32 or w == 0 or h == 0:
            return None
        data = Q.CGDataProviderCopyData(Q.CGImageGetDataProvider(img))
        return Image.frombuffer("RGB", (w, h), bytes(data), "raw", "BGRX", Q.CGImageGetBytesPerRow(img), 1)
    except Exception:  # noqa: BLE001  不是 macOS / PyObjC 出问题：退回整屏截图（高亮层已在 hidden() 里清空）
        return None


def grab() -> Image.Image:
    """截主显示器整屏（不含 ShowMe 高亮层）。排除窗口失败时用 mss，再不行改用系统的 screencapture。"""
    if EXCLUDE_WINDOW_IDS:
        img = _grab_excluding(EXCLUDE_WINDOW_IDS)
        if img is not None:
            return img
    try:
        with getattr(mss, "MSS", mss.mss)() as s:
            shot = s.grab(s.monitors[1])
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:  # noqa: BLE001
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            subprocess.run(["screencapture", "-x", "-m", path], check=True, timeout=10)
            with Image.open(path) as img:
                return img.convert("RGB")
        finally:
            os.unlink(path)


def to_jpeg_b64(img: Image.Image, max_side: int = MAX_SIDE, quality: int = JPEG_QUALITY) -> str:
    """长边缩到 max_side，转 JPEG，返回不带 data: 前缀的 base64。"""
    img = img.convert("RGB")
    scale = max_side / max(img.size)
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def to_small_gray(img: Image.Image, side: int = 64) -> np.ndarray:
    """缩成 side x side 灰度图，float32，0–1。用来检测画面变化。"""
    small = img.convert("L").resize((side, side), Image.BILINEAR)
    return np.asarray(small, dtype=np.float32) / 255.0
