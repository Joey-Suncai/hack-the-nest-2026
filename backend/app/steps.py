"""/api/next-step 的数据结构、框修正和 mock。真实 Gemini 版 next_step 在 A2 加。"""

from __future__ import annotations

import base64
import binascii
import io
import os
from typing import Literal

from PIL import Image
from pydantic import BaseModel

from app import gemini


class HistoryItem(BaseModel):
    instruction: str
    ok: bool | None = None


class NextStepIn(BaseModel):
    app: str
    goal: str
    lang: Literal["zh", "en"] = "zh"
    screenshot: str
    history: list[HistoryItem] = []
    expect: str | None = None
    guide: list[str] | None = None
    user_note: str | None = None
    frontmost_app: str | None = None


class NextStepOut(BaseModel):
    status: Literal["next", "done", "unsure"]
    previous_ok: bool | None
    instruction: str
    target_label: str | None
    box: list[int] | None
    expect: str | None


def decode_screenshot(b64: str) -> bytes:
    """base64 → 图片字节，并用 Pillow 确认是有效图片。"""
    try:
        if b64.startswith("data:"):
            b64 = b64.split(",", 1)[1]
        data = base64.b64decode(b64, validate=True)
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return data
    except (binascii.Error, ValueError, OSError, IndexError, Image.UnidentifiedImageError):
        raise ValueError("screenshot 不是有效的图片")


def normalize_box(box: list | None) -> list[int] | None:
    """[ymin, xmin, ymax, xmax] 0–1000：取整、修正顺序、裁到范围内；无效返回 None。"""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        y1, x1, y2, x2 = (min(1000, max(0, round(float(v)))) for v in box)
    except (TypeError, ValueError):
        return None
    ymin, ymax = sorted((y1, y2))
    xmin, xmax = sorted((x1, x2))
    if ymax - ymin < 1 or xmax - xmin < 1:
        return None
    return [ymin, xmin, ymax, xmax]


_FALLBACK = {
    "zh": ("看不出下一步该点哪里，请确认软件窗口在最前面再试一次", "下一步"),
    "en": ("I can't tell what to click next. Bring the app window to the front and try again.", "Next step."),
}


def finalize(out: NextStepOut, lang: str = "zh") -> NextStepOut:
    """保证传给客户端的数据合法：next 一定有有效框，done / unsure 一定没有框。"""
    box = normalize_box(out.box)
    status = out.status
    if status == "next" and box is None:
        status = "unsure"
    if status != "next":
        box = None
    unsure_msg, next_msg = _FALLBACK.get(lang, _FALLBACK["zh"])
    instruction = (out.instruction or "").strip() or (unsure_msg if status == "unsure" else next_msg)
    return out.model_copy(update={"status": status, "box": box, "instruction": instruction})


_MOCK_BOXES = [[450, 450, 550, 550], [300, 600, 380, 720], [600, 250, 680, 400]]


def mock_next_step(body: NextStepIn) -> NextStepOut:
    n = len(body.history)
    previous_ok = True if n else None
    if n >= len(_MOCK_BOXES):
        return NextStepOut(status="done", previous_ok=previous_ok, instruction="[MOCK] 完成了！",
                           target_label=None, box=None, expect=None)
    return NextStepOut(
        status="next",
        previous_ok=previous_ok,
        instruction=f"[MOCK] 第 {n + 1} 步：点这里",
        target_label=f"按钮{n + 1}",
        box=_MOCK_BOXES[n],
        expect=f"[MOCK] 第 {n + 1} 步完成",
    )


_LANG_NAME = {"zh": "简体中文", "en": "English"}
_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def image_mime(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as img:
        return _MIME.get(img.format or "", "image/jpeg")


def build_prompt(body: NextStepIn) -> str:
    if body.history:
        history = "\n".join(
            f"{i}. {h.instruction}" + ("" if h.ok is None else ("（完成）" if h.ok else "（没完成）"))
            for i, h in enumerate(body.history, 1)
        )
    else:
        history = "（无，这是第一步）"
    guide = "\n".join(f"- {g}" for g in body.guide) if body.guide else "（无）"
    return gemini.load_prompt(
        "next_step",
        app=body.app.strip() or "当前软件",
        goal=body.goal.strip(),
        lang_name=_LANG_NAME[body.lang],
        history=history,
        expect=body.expect or "（无）",
        guide=guide,
        user_note=(body.user_note or "").strip() or "（无）",
        frontmost_app=(body.frontmost_app or "").strip() or "（未知）",
    )


# ---------- 两步定位：小框裁剪放大后再问一次 ----------
REFINE = os.getenv("SHOWME_REFINE", "0").strip().lower() in ("1", "true", "yes")
REFINE_MAX_AREA = 20000  # 框面积（0–1000²）小于这个才精修，约占屏幕 2%
CROP_MIN_SIZE = 200  # 裁剪区域最小边长（0–1000），保证有上下文
CROP_LONG_EDGE = 1024  # 裁剪图放大到的长边像素


class RefineOut(BaseModel):
    found: bool
    box: list[int] | None


def crop_region(box: list[int], pad: float = 1.0) -> list[int]:
    """框四周各扩 pad 倍框的高 / 宽，至少 CROP_MIN_SIZE，裁到 0–1000。"""
    y1, x1, y2, x2 = box

    def span(a, b):
        size = b - a
        lo, hi = a - size * pad, b + size * pad
        if hi - lo < CROP_MIN_SIZE:
            mid = (a + b) / 2
            lo, hi = mid - CROP_MIN_SIZE / 2, mid + CROP_MIN_SIZE / 2
        return round(max(0, lo)), round(min(1000, hi))

    ry1, ry2 = span(y1, y2)
    rx1, rx2 = span(x1, x2)
    return [ry1, rx1, ry2, rx2]


def map_box_from_crop(inner: list[int], region: list[int]) -> list[int]:
    """裁剪图里的 0–1000 框 → 整图的 0–1000 框。"""
    ry1, rx1, ry2, rx2 = region
    h, w = ry2 - ry1, rx2 - rx1
    y1, x1, y2, x2 = inner
    return [round(ry1 + y1 * h / 1000), round(rx1 + x1 * w / 1000),
            round(ry1 + y2 * h / 1000), round(rx1 + x2 * w / 1000)]


def crop_image(img_bytes: bytes, region: list[int]) -> bytes:
    """按 0–1000 区域裁剪，放大到长边 CROP_LONG_EDGE，返回 JPEG。"""
    with Image.open(io.BytesIO(img_bytes)) as img:
        img = img.convert("RGB")
        W, H = img.size
        ry1, rx1, ry2, rx2 = region
        crop = img.crop((rx1 * W // 1000, ry1 * H // 1000, rx2 * W // 1000, ry2 * H // 1000))
        scale = CROP_LONG_EDGE / max(crop.size)
        if scale > 1:
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, "JPEG", quality=90)
        return buf.getvalue()


def _area(box: list[int]) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


def refine_box(img: bytes, out: NextStepOut) -> NextStepOut:
    """小框裁剪放大后再定位一次；任何失败都保留原框。"""
    if not REFINE or out.status != "next" or not out.box or _area(out.box) >= REFINE_MAX_AREA:
        return out
    try:
        region = crop_region(out.box)
        prompt = gemini.load_prompt("refine", target_label=out.target_label or out.instruction,
                                    instruction=out.instruction)
        r = gemini.generate_json(prompt, RefineOut, files=[(crop_image(img, region), "image/jpeg")])
        inner = normalize_box(r.box) if r.found else None
        if inner is None:
            return out
        return out.model_copy(update={"box": map_box_from_crop(inner, region)})
    except Exception as e:  # 精修只是锦上添花，失败不影响主流程
        print(f"[refine] skipped: {type(e).__name__}: {e}")
        return out


def next_step(body: NextStepIn) -> NextStepOut:
    """真实 Gemini 版：截图 + 进度 → 下一步（小框再精修一次）。结果一定经过 finalize。"""
    img = decode_screenshot(body.screenshot)
    out = gemini.generate_json(build_prompt(body), NextStepOut, files=[(img, image_mime(img))])
    if not body.history:
        out = out.model_copy(update={"previous_ok": None})
    out = finalize(out, body.lang)
    return finalize(refine_box(img, out), body.lang)
