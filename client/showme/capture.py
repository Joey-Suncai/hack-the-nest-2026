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


def grab() -> Image.Image:
    """截主显示器整屏。mss 失败时改用系统的 screencapture。"""
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
