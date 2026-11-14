"""定位准确度评测：逐张截图调用真实 next_step，统计命中率（预测框中心落在正确框里）、IoU、耗时。

labels.json：[{"file": "x.png", "app": "...", "goal": "...", "history": ["已做的步骤", ...],
               "target_px": [x1, y1, x2, y2]}]   # 像素坐标，相对原图
运行：cd backend && uv run python eval/eval_boxes.py [文件名关键字]
"""
import base64
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from app import gemini, steps  # noqa: E402

CASES = Path(__file__).parent / "cases"


def to_jpeg_b64(path: Path, long_edge: int = 1280) -> tuple[str, tuple[int, int]]:
    """模拟客户端：缩到长边 1280 的 JPEG。返回 base64 和原图尺寸。"""
    with Image.open(path) as img:
        size = img.size
        img = img.convert("RGB")
        scale = long_edge / max(size)
        if scale < 1:
            img = img.resize((round(size[0] * scale), round(size[1] * scale)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode(), size


def px_to_norm(px, size):
    x1, y1, x2, y2 = px
    w, h = size
    return [y1 * 1000 / h, x1 * 1000 / w, y2 * 1000 / h, x2 * 1000 / w]


def iou(a, b):
    ih = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iw = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ih * iw
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])  # noqa: E731
    return inter / (area(a) + area(b) - inter) if inter else 0.0


def main():
    if gemini.MOCK:
        sys.exit("GEMINI_MOCK=1 时评测没有意义，用 GEMINI_MOCK=0 运行")
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    cases = [c for c in json.loads((CASES / "labels.json").read_text()) if key in c["file"]]
    hits, times, rows = 0, [], []
    for c in cases:
        b64, size = to_jpeg_b64(CASES / c["file"])
        truth = px_to_norm(c["target_px"], size)
        body = steps.NextStepIn(
            app=c["app"], goal=c["goal"], lang=c.get("lang", "zh"), screenshot=b64,
            history=[steps.HistoryItem(instruction=h, ok=True) for h in c.get("history", [])],
        )
        t0 = time.perf_counter()
        try:
            out = steps.next_step(body)
            err = ""
        except gemini.GeminiError as e:
            out, err = None, e.message
        dt = time.perf_counter() - t0
        times.append(dt)
        hit, score = False, 0.0
        if out and out.box:
            cy, cx = (out.box[0] + out.box[2]) / 2, (out.box[1] + out.box[3]) / 2
            hit = truth[0] <= cy <= truth[2] and truth[1] <= cx <= truth[3]
            score = iou(out.box, truth)
        hits += hit
        detail = err or f"{out.status} box={out.box} label={out.target_label!r} 「{out.instruction}」"
        print(f"{'✅' if hit else '❌'} {c['file']:<32} IoU={score:.2f} {dt:5.1f}s  {detail}")
    n = len(cases)
    if n:
        print(f"\n模型 {gemini.MODEL}：命中率 {hits}/{n} = {hits / n:.0%}，平均耗时 {sum(times) / n:.1f}s，"
              f"最慢 {max(times):.1f}s")


if __name__ == "__main__":
    main()
