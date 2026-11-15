"""macOS Vision 文字识别：在新画面里找 next_guess.label，秒出框（不等 Gemini）。

Vision 的框是 0–1 归一化、原点在左下角；我们统一用 [ymin, xmin, ymax, xmax] 0–1000、原点在左上角。
"""

import difflib
import io
import re
import time

from PIL import Image

MIN_SIMILARITY = 0.6  # 文字不完全一样时，相似度至少多少才算找到


def vision_to_box(x: float, y: float, w: float, h: float) -> list[int]:
    """Vision 框（左下角原点，0–1）→ [ymin, xmin, ymax, xmax]（左上角原点，0–1000）。"""
    return [round((1 - y - h) * 1000), round(x * 1000), round((1 - y) * 1000), round((x + w) * 1000)]


def find_text_boxes(img: Image.Image, label: str | None = None, fast: bool = True) -> list[tuple[str, list[int]]]:
    """识别整张图里的文字，返回 [(文字, 框)]。

    给了 label 时，如果某一行里包含 label，就用 Vision 算出 label 那几个字自己的框（而不是整行）。
    """
    import Vision
    from Foundation import NSData, NSMakeRange

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    raw = buf.getvalue()
    data = NSData.dataWithBytes_length_(raw, len(raw))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast if fast else Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    req.setRecognitionLanguages_(["en-US"])  # demo 全英文；fast 模式对中文支持不好
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision 识别失败：{err}")
    out = []
    low_label = _norm(label or "")
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        cand = cands[0]
        text = str(cand.string())
        bb = obs.boundingBox()
        box = vision_to_box(bb.origin.x, bb.origin.y, bb.size.width, bb.size.height)
        i = text.lower().find(low_label) if low_label else -1
        if i >= 0 and len(low_label) < len(text):
            try:
                rect, _err = cand.boundingBoxForRange_error_(NSMakeRange(i, len(low_label)), None)
                if rect is not None:
                    sb = rect.boundingBox()
                    box = vision_to_box(sb.origin.x, sb.origin.y, sb.size.width, sb.size.height)
            except Exception:  # noqa: BLE001  拿不到子范围就用整行
                pass
        out.append((text, box))
    return out


_EDGE = re.compile(r"^[\W_]+|[\W_]+$")  # 首尾的标点 / 符号：… ... → : 引号 括号
MIN_CONTAINS = 3  # 「包含」匹配时，较短的一方至少几个字符
NEAR_RATIO = 0.7  # 多个候选时：最近的要比第二近的近这么多（距离 ≤ 0.7 倍）才算分得出来


def _norm(s: str) -> str:
    """转小写、去掉首尾标点和符号、合并空格。"R Markdown..." → "r markdown"，"Next Section →" → "next section"。"""
    s = " ".join((s or "").lower().replace("…", " ").split())
    return " ".join(_EDGE.sub("", s).split())


def _contains(a: str, b: str) -> bool:
    """较短的一方（至少 MIN_CONTAINS 个字符）作为完整的词出现在较长的一方里（"code" 不算在 "encode" 里）。"""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < MIN_CONTAINS:
        return False
    return re.search(rf"(?<!\w){re.escape(short)}(?!\w)", long_) is not None


def _center(b: list[int]) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _pick(boxes: list[list[int]], near: list[int] | None) -> list[int] | None:
    """一个候选就用它；多个时选离上一个框最近的，而且要明显更近，否则分不出来 → None（等 Gemini）。"""
    if len(boxes) == 1:
        return boxes[0]
    if not boxes or not near:
        return None
    cy, cx = _center(near)
    ds = sorted(((((y - cy) ** 2 + (x - cx) ** 2) ** 0.5, b) for b in boxes for y, x in [_center(b)]), key=lambda d: d[0])
    return ds[0][1] if ds[0][0] <= NEAR_RATIO * ds[1][0] else None


def best_match(items: list[tuple[str, list[int]]], label: str | None, strict: bool = True,
               near: list[int] | None = None) -> list[int] | None:
    """在识别结果里找 label。两边先规范化（_norm）。

    strict（默认，秒出框用）：规范化后完全相同 > 一方包含另一方（较短的至少 3 个字符、整词）。
      同一档里有多个候选：优先离上一个框（near）近的；还分不出来就放弃（宁可等 Gemini，也不能框错——
      联调时包含匹配曾框到同名的别处文字，比如左边栏图标 vs 右边的 "Extensions" 标题）。
    strict=False：完全一样 > 包含（越短越好）> 相似度最高。
    """
    want = _norm(label or "")
    if not want:
        return None
    exact = [b for t, b in items if _norm(t) == want]
    if strict:
        if exact:
            return _pick(exact, near)
        return _pick([b for t, b in items if _norm(t) and _contains(_norm(t), want)], near)
    if exact:
        return exact[0]
    contains = sorted(((len(t), b) for t, b in items if want in _norm(t)), key=lambda x: x[0])
    if contains:
        return contains[0][1]
    scored = [(difflib.SequenceMatcher(None, want, _norm(t)).ratio(), b) for t, b in items]
    scored = [s for s in scored if s[0] >= MIN_SIMILARITY]
    if scored:
        return max(scored, key=lambda s: s[0])[1]
    return None


def locate(img: Image.Image, label: str | None, near: list[int] | None = None) -> tuple[list[int] | None, float]:
    """一步到位：识别 + 找 label。near：上一个框（多个候选时选离它近的）。返回 (框或 None, 用了多少毫秒)。"""
    t0 = time.perf_counter()
    box = None
    try:
        box = best_match(find_text_boxes(img, label, fast=True), label, near=near)  # 约 50 ms
        if box is None:  # 小字 fast 模式可能认不出，再用 accurate 试一次（约 230 ms）
            box = best_match(find_text_boxes(img, label, fast=False), label, near=near)
    except Exception as e:  # noqa: BLE001  OCR 出问题就等 Gemini
        print(f"[ocr] 识别出错：{e}", flush=True)
    return box, (time.perf_counter() - t0) * 1000


def warmup() -> float:
    """启动时在后台先跑一次：Vision 第一次调用要初始化（约 0.5 秒），不热身的话第一次秒出框会慢。返回毫秒。"""
    from PIL import ImageDraw

    t0 = time.perf_counter()
    img = Image.new("RGB", (240, 60), "white")
    ImageDraw.Draw(img).text((10, 20), "Warm up", fill="black")
    try:
        find_text_boxes(img, fast=True)
        find_text_boxes(img, fast=False)
    except Exception as e:  # noqa: BLE001
        print(f"[ocr] 热身失败：{e}", flush=True)
    return (time.perf_counter() - t0) * 1000
