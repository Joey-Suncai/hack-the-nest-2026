"""/api/next-step 的数据结构、框修正和 mock。真实 Gemini 版 next_step 在 A2 加。"""

from __future__ import annotations

import base64
import binascii
import io
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


def finalize(out: NextStepOut) -> NextStepOut:
    """保证传给客户端的数据合法：next 一定有有效框，done / unsure 一定没有框。"""
    box = normalize_box(out.box)
    status = out.status
    if status == "next" and box is None:
        status = "unsure"
    if status != "next":
        box = None
    instruction = (out.instruction or "").strip() or (
        "看不出下一步该点哪里，请确认软件窗口在最前面再试一次" if status == "unsure" else "下一步"
    )
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
    )


def next_step(body: NextStepIn) -> NextStepOut:
    """真实 Gemini 版：截图 + 进度 → 下一步。结果一定经过 finalize。"""
    img = decode_screenshot(body.screenshot)
    out = gemini.generate_json(build_prompt(body), NextStepOut, files=[(img, image_mime(img))])
    if not body.history:
        out = out.model_copy(update={"previous_ok": None})
    return finalize(out)
