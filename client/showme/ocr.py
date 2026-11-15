"""macOS Vision 文字识别：在新画面里找 next_guess.label，秒出框（不等 Gemini）。

Vision 的框是 0–1 归一化、原点在左下角；我们统一用 [ymin, xmin, ymax, xmax] 0–1000、原点在左上角。
"""

import difflib
import io
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
    low_label = (label or "").strip().lower()
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


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("…", "").replace("...", "").split())


def best_match(items: list[tuple[str, list[int]]], label: str | None) -> list[int] | None:
    """在识别结果里找和 label 最接近的那一个（大小写不敏感）。

    优先级：完全一样 > 包含 label（越短越好，避免匹配到一大段话）> 相似度 ≥ MIN_SIMILARITY 里最高的。找不到返回 None。
    """
    if not label or not _norm(label):
        return None
    want = _norm(label)
    exact = [b for t, b in items if _norm(t) == want]
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


def locate(img: Image.Image, label: str | None) -> tuple[list[int] | None, float]:
    """一步到位：识别 + 找 label。返回 (框或 None, 用了多少毫秒)。出错返回 (None, ms)。"""
    t0 = time.perf_counter()
    box = None
    try:
        box = best_match(find_text_boxes(img, label, fast=True), label)  # 约 50 ms
        if box is None:  # 小字 fast 模式可能认不出，再用 accurate 试一次（约 230 ms）
            box = best_match(find_text_boxes(img, label, fast=False), label)
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
