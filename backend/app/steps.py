"""/api/next-step 的数据结构、框修正和 mock。真实 Gemini 版 next_step 在 A2 加。"""

from __future__ import annotations

import base64
import binascii
import io
import os
import time
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ValidationError, field_validator

from app import gemini


class HistoryItem(BaseModel):
    instruction: str
    ok: bool | None = None


class Element(BaseModel):
    """客户端用 macOS 辅助功能读到的真实控件，box 和返回的 box 同一个坐标系（0–1000）。"""

    id: int
    role: str
    name: str | None = None
    box: list[int]


MAX_ELEMENTS = 250


class Confirmed(BaseModel):
    """客户端确认用户做了上一步（比如点击落在高亮的元素里）。detail = 之后辅助功能读到的，如 "focused AXComboBox '开始时间'"。"""

    kind: Literal["click", "typing", "enter"]
    target: str | None = None
    detail: str | None = None


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
    elements: list[Element] | None = None  # 读不到辅助功能时为 null，行为和以前完全一样
    deep: bool = False  # 客户端要求多想一想（用 NEXT_STEP_DEEP_THINKING）
    confirmed: Confirmed | None = None  # 客户端已确认上一步做了
    app_switched_from: str | None = None  # 最前面的 App 刚换了：这里是之前那个，frontmost_app 是现在的

    @field_validator("confirmed", mode="before")
    @classmethod
    def _lenient_confirmed(cls, v):
        """confirmed 格式不对就当没传，不让整个请求 422（demo 现场不能崩）。"""
        if v is None or isinstance(v, Confirmed):
            return v
        try:
            return Confirmed.model_validate(v)
        except ValidationError:
            return None


class NextGuess(BaseModel):
    """预测这一步点完之后，下一步要点的元素（客户端用本地 OCR 找 label 秒出框）。"""

    label: str
    instruction: str
    expect: str | None = None


class NextStepOut(BaseModel):
    # 字段顺序就是模型输出的顺序：先想清楚情况（situation / reasoning / plan），再决定 status / box
    situation: str | None = None  # 一句话：屏幕上现在是什么情况
    reasoning: str | None = None  # 1–2 句：情况和目标的关系、为什么是这一步
    plan: list[str] | None = None  # 剩下的步骤（包括这一步），最多 8 条
    app: str | None = None  # 目标元素所在的 App
    status: Literal["next", "done", "unsure"]
    previous_ok: bool | None
    instruction: str
    target_label: str | None
    box: list[int] | None
    expect: str | None
    next_guess: NextGuess | None = None
    element_id: int | None = None  # 选中的 elements 里的编号；null = 框是 Gemini 自己给的


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


def find_element(elements: list[Element] | None, element_id: int | None) -> Element | None:
    """按编号找请求里的控件，框无效也当没找到。"""
    if element_id is None or not elements:
        return None
    for e in elements:
        if e.id == element_id and normalize_box(e.box) is not None:
            return e
    return None


MAX_PLAN = 8
MAX_PLAN_ITEM = 80


def _text(v, limit: int = 300) -> str | None:
    """去掉首尾空白；不是字符串或为空就返回 None。"""
    if not isinstance(v, str):
        return None
    v = " ".join(v.split())[:limit].strip()
    return v or None


def clean_plan(plan) -> list[str] | None:
    if not isinstance(plan, (list, tuple)):
        return None
    items = [t for t in (_text(p, MAX_PLAN_ITEM) for p in plan) if t][:MAX_PLAN]
    return items or None


def finalize(out: NextStepOut, lang: str = "zh", elements: list[Element] | None = None) -> NextStepOut:
    """保证传给客户端的数据合法：next 一定有有效框，done / unsure 一定没有框。
    element_id 对得上请求里的控件时，用控件的精确框和名字；对不上就清空 element_id、保留模型的框。"""
    el = find_element(elements, out.element_id) if out.status == "next" else None
    if el is not None:
        name = (el.name or "").strip()
        out = out.model_copy(update={"box": el.box, "target_label": name or out.target_label})
    box = normalize_box(out.box)
    status = out.status
    if status == "next" and box is None:
        status = "unsure"
    if status != "next":
        box = None
    unsure_msg, next_msg = _FALLBACK.get(lang, _FALLBACK["zh"])
    instruction = (out.instruction or "").strip() or (unsure_msg if status == "unsure" else next_msg)
    guess = out.next_guess
    if status != "next" or not guess or not (guess.label or "").strip() or not (guess.instruction or "").strip():
        guess = None
    else:
        guess = guess.model_copy(update={"label": guess.label.strip(), "instruction": guess.instruction.strip()})
    element_id = el.id if el is not None and status == "next" else None
    return out.model_copy(update={"status": status, "box": box, "instruction": instruction, "next_guess": guess,
                                  "element_id": element_id, "situation": _text(out.situation),
                                  "reasoning": _text(out.reasoning),
                                  "plan": clean_plan(out.plan) if status != "done" else None,
                                  "app": _text(out.app, 80)})


_MOCK_BOXES = [[450, 450, 550, 550], [300, 600, 380, 720], [600, 250, 680, 400]]


def mock_next_step(body: NextStepIn) -> NextStepOut:
    out = _mock_step(body)
    if out.status == "done":
        return out
    n = len(body.history)
    switched = f"，刚从 {body.app_switched_from} 切过来" if body.app_switched_from else ""
    return out.model_copy(update={
        "situation": f"[MOCK] 第 {n + 1} 步的画面{switched}",
        "reasoning": "[MOCK] 上一步已确认完成，继续下一步" if body.confirmed else "[MOCK] 按计划继续",
        "plan": [f"[MOCK] 第 {i + 1} 步" for i in range(n, len(_MOCK_BOXES))],
        "app": (body.frontmost_app or body.app or "").strip() or None,
    })


def _mock_step(body: NextStepIn) -> NextStepOut:
    n = len(body.history)
    previous_ok = True if n else None
    if n >= len(_MOCK_BOXES):
        return NextStepOut(status="done", previous_ok=previous_ok, instruction="[MOCK] 完成了！",
                           target_label=None, box=None, expect=None)
    named = next((e for e in body.elements or [] if (e.name or "").strip()), None)
    if named is not None:  # 有 elements：选第一个有名字的控件，方便客户端测整条链路
        return NextStepOut(status="next", previous_ok=previous_ok, instruction=f"[MOCK] 点击 {named.name.strip()}",
                           target_label=named.name.strip(), box=named.box, expect="[MOCK] 画面有变化",
                           element_id=named.id)
    return NextStepOut(
        status="next",
        previous_ok=previous_ok,
        instruction=f"[MOCK] 第 {n + 1} 步：点这里",
        target_label=f"按钮{n + 1}",
        box=_MOCK_BOXES[n],
        expect=f"[MOCK] 第 {n + 1} 步完成",
        next_guess=NextGuess(label=f"按钮{n + 2}", instruction=f"[MOCK] 第 {n + 2} 步：点这里",
                             expect=f"[MOCK] 第 {n + 2} 步完成") if n + 1 < len(_MOCK_BOXES) else None,
    )


_LANG_NAME = {"zh": "简体中文", "en": "English"}
_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def image_mime(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as img:
        return _MIME.get(img.format or "", "image/jpeg")


def _clean(text: str, limit: int = 60) -> str:
    return " ".join(text.split()).replace('"', "'")[:limit]


def elements_section(elements: list[Element]) -> str:
    """一行一个控件：#12 AXButton "New Tab" [40,950,62,966]"""
    lines = []
    for e in elements[:MAX_ELEMENTS]:
        name = _clean(e.name or "")
        label = f'"{name}"' if name else "null"
        lines.append(f"#{e.id} {_clean(e.role, 30)} {label} [{','.join(str(v) for v in e.box)}]")
    return gemini.load_prompt("next_step_elements", elements="\n".join(lines))


_KIND = {"click": "点击", "typing": "打字", "enter": "按回车"}


def context_section(body: NextStepIn) -> str:
    """可选的上下文（换了 App、客户端确认上一步做了）；都没有时是空字符串，prompt 和以前一样。"""
    lines = []
    prev = _clean(body.app_switched_from or "", 80)
    if prev:
        now = _clean(body.frontmost_app or "", 80) or "另一个 App"
        lines.append(f"最前面的 App 刚刚换了：从「{prev}」换成了「{now}」（按下面的规则 D 判断新 App 是不是流程的一部分）。")
    c = body.confirmed
    if c is not None:
        what = _KIND.get(c.kind, c.kind)
        target = _clean(c.target or "", 80)
        detail = _clean(c.detail or "", 120)
        line = f"客户端已确认上一步：用户确实做了上一步（{what}" + (f"「{target}」" if target else "") + "）"
        line += f"，之后辅助功能读到：{detail}。" if detail else "。"
        lines.append(line + "（按下面的规则 E 处理）")
    return "".join(f"\n{ln}\n" for ln in lines)


def build_prompt(body: NextStepIn) -> str:
    if body.history:
        history = "\n".join(
            f"{i}. {h.instruction}" + ("" if h.ok is None else ("（完成）" if h.ok else "（没完成）"))
            for i, h in enumerate(body.history, 1)
        )
    else:
        history = "（无，这是第一步）"
    guide = "\n".join(f"- {g}" for g in body.guide) if body.guide else "（无）"
    prompt = gemini.load_prompt(
        "next_step",
        app=body.app.strip() or "当前软件",
        goal=body.goal.strip(),
        lang_name=_LANG_NAME[body.lang],
        history=history,
        expect=body.expect or "（无）",
        guide=guide,
        user_note=(body.user_note or "").strip() or "（无）",
        frontmost_app=(body.frontmost_app or "").strip() or "（未知）",
        context=context_section(body),
    )
    if body.elements:  # 没有 elements 时 prompt 和以前一字不差（评测结果可比）
        prompt += "\n" + elements_section(body.elements)
    return prompt


# ---------- 两步定位：小框裁剪放大后再问一次 ----------
REFINE = os.getenv("SHOWME_REFINE", "0").strip().lower() in ("1", "true", "yes")
# 上一步没成功（用户点不中 / 说点错了）时自动精修：第一次保持快，重试时更准（日历 / 图标栏差一格的情况）
REFINE_ON_RETRY = os.getenv("SHOWME_REFINE_ON_RETRY", "1").strip().lower() in ("1", "true", "yes")
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


def refine_box(img: bytes, out: NextStepOut, force: bool = False) -> NextStepOut:
    """小框裁剪放大后再定位一次；任何失败都保留原框。"""
    if not (REFINE or force) or out.status != "next" or not out.box or _area(out.box) >= REFINE_MAX_AREA:
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


# next-step 的思考量："" = 模型默认；minimal / low / medium / high
# 评测：default 93% / 4.4s，low 93% / 2.2s，minimal 85% / 1.3s → 默认 low
THINKING = os.getenv("NEXT_STEP_THINKING", "low").strip().lower() or None
# 需要真正推理的时候（第一步、换了 App、上一步没成功、用户说了话、客户端要求）可以多想一点。
# 默认仍是 low：评测里 medium 平均 8.2 s/步，low 下 4 个推理用例照样 4/4、平均 2.9 s（推理主要靠 prompt）。
# 想更深就设 NEXT_STEP_DEEP_THINKING=medium
DEEP_THINKING = os.getenv("NEXT_STEP_DEEP_THINKING", "low").strip().lower() or None
LAST_TIMING: dict[str, float] = {}  # 最近一次 next_step 各阶段耗时（毫秒），评测脚本读
LAST_THINKING: dict[str, str | None] = {}  # 最近一次用的思考量和原因，评测脚本读


def choose_thinking(body: NextStepIn) -> tuple[str | None, str]:
    """返回 (thinking, 原因)。"""
    reasons = []
    if body.deep:
        reasons.append("deep requested")
    if not body.history:
        reasons.append("first step")
    if (body.user_note or "").strip():
        reasons.append("user note")
    if (body.app_switched_from or "").strip():
        reasons.append("app switched")
    if body.history and body.history[-1].ok is False:
        reasons.append("previous step failed")
    if reasons:
        return DEEP_THINKING, ", ".join(reasons)
    return THINKING, "routine"


def next_step(body: NextStepIn) -> NextStepOut:
    """真实 Gemini 版：截图 + 进度 → 下一步（小框再精修一次）。结果一定经过 finalize。"""
    t0 = time.perf_counter()
    img = decode_screenshot(body.screenshot)
    mime = image_mime(img)
    prompt = build_prompt(body)
    t1 = time.perf_counter()
    thinking, why = choose_thinking(body)
    LAST_THINKING.clear()
    LAST_THINKING.update(level=thinking, reason=why)
    print(f"[next-step] thinking={thinking or 'default'} (reason: {why})")
    out = gemini.generate_json(prompt, NextStepOut, files=[(img, mime)], thinking=thinking)
    t2 = time.perf_counter()
    if not body.history:
        out = out.model_copy(update={"previous_ok": None})
    out = finalize(out, body.lang, body.elements)
    if body.elements:
        el = find_element(body.elements, out.element_id)
        print(f"[next-step] element #{el.id} {el.role} {el.name!r}" if el else "[next-step] own box (no element)")
    # 上一步没成功：history 标了 ok=false、或者用户刚说了什么（点错了 / 没反应）、或者模型判断上一步没完成
    retry = REFINE_ON_RETRY and bool(body.history) and (
        body.history[-1].ok is False or bool((body.user_note or "").strip()) or out.previous_ok is False
    )
    if out.element_id is None:  # 选中了控件就不精修：框已经是精确的
        out = finalize(refine_box(img, out, force=retry), body.lang)
    t3 = time.perf_counter()
    LAST_TIMING.clear()
    LAST_TIMING.update(prep=(t1 - t0) * 1000, gemini=(t2 - t1) * 1000, post=(t3 - t2) * 1000, total=(t3 - t0) * 1000)
    print(f"[next-step] 解码+prompt {LAST_TIMING['prep']:.0f}ms · Gemini {LAST_TIMING['gemini']:.0f}ms · "
          f"修框 {LAST_TIMING['post']:.0f}ms · 合计 {LAST_TIMING['total']:.0f}ms（thinking={thinking or 'default'}）")
    return out
