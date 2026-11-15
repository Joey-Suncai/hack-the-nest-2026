"""定位准确度评测：逐张截图调用真实 next_step，统计命中率（预测框中心落在正确框里）、IoU、耗时。

labels.json：[{"file": "x.png", "app": "...", "goal": "...", "history": ["已做的步骤", ...],
               "target_px": [x1, y1, x2, y2]}]   # 像素坐标，相对原图
  任务已完成的截图：不写 target_px，改写 "expect_status": "done"（模型返回 done 才算命中）
  需要用户先做点什么的截图（如 Zoom 工具栏自动隐藏）：写 "expect_status": "unsure"
  可选 "app_en" / "goal_en" / "history_en"：--lang en 时用英文的目标和历史（模拟客户端发英文）
  可选 "expect" / "guide" / "elements" / "confirmed" / "app_switched_from" / "deep"：原样放进请求（真实日志的 case）
  可选 "avoid_label"：字符串或列表，target_label 含有其中任何一个就算错
运行：cd backend && uv run python eval/eval_boxes.py [文件名关键字] [--lang en|zh]
  --lang 强制所有 case 用这个语言（不写就用每个 case 自己的 lang，默认 zh）
"""
import argparse
import base64
import io
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from app import gemini, steps  # noqa: E402

CASES = Path(__file__).parent / "cases"
CJK = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")


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
    ap = argparse.ArgumentParser()
    ap.add_argument("key", nargs="?", default="", help="只跑文件名包含这个关键字的 case")
    ap.add_argument("--lang", choices=["zh", "en"], help="强制所有 case 用这个语言")
    ap.add_argument("--group", help="只跑这一组（labels.json 里的 group，没写的算 tuned）")
    a = ap.parse_args()
    cases = [c for c in json.loads((CASES / "labels.json").read_text()) if a.key in c["file"]]
    if a.group:
        cases = [c for c in cases if c.get("group", "tuned") == a.group]
    hits, times, ious, cjk_bad = 0, [], [], 0
    stage = {"prep": [], "gemini": [], "post": []}
    by_group: dict[str, list] = {}
    guess_given, guess_ok, guess_checked = 0, 0, 0
    deep_rows: list = []
    for c in cases:
        b64, size = to_jpeg_b64(CASES / c["file"])
        want_status = c.get("expect_status")  # "done" / "unsure" / None（要框）
        truth = None if want_status else px_to_norm(c["target_px"], size)
        lang = a.lang or c.get("lang", "zh")
        app, goal, history = c["app"], c["goal"], c.get("history", [])
        if lang == "en":
            app, goal, history = c.get("app_en", app), c.get("goal_en", goal), c.get("history_en", history)
        items = [steps.HistoryItem(**h) for h in c["history_items"]] if c.get("history_items") else \
            [steps.HistoryItem(instruction=h, ok=True) for h in history]
        body = steps.NextStepIn(app=app, goal=goal, lang=lang, screenshot=b64, history=items,
                                user_note=c.get("user_note"), frontmost_app=c.get("frontmost_app"),
                                # 可选：真实请求里的这些字段（client/logs 里的 case），没写就和以前一样
                                expect=c.get("expect"), guide=c.get("guide"), elements=c.get("elements"),
                                confirmed=c.get("confirmed"), app_switched_from=c.get("app_switched_from"),
                                deep=c.get("deep", False))
        t0 = time.perf_counter()
        try:
            out = steps.next_step(body)
            err = ""
        except gemini.GeminiError as e:
            out, err = None, e.message
        dt = time.perf_counter() - t0
        times.append(dt)
        if not err:
            for k in stage:
                stage[k].append(steps.LAST_TIMING.get(k, 0))
        hit, score = False, 0.0
        if want_status:
            hit = bool(out) and out.status == want_status
        elif c.get("accept_unsure") and out and out.status == "unsure":
            hit = True
        elif out and out.box:
            cy, cx = (out.box[0] + out.box[2]) / 2, (out.box[1] + out.box[3]) / 2
            hit = truth[0] <= cy <= truth[2] and truth[1] <= cx <= truth[3]
            for alt in c.get("alt_target_px", []):  # 其他也算对的目标（比如改用聚焦搜索）
                an = px_to_norm(alt, size)
                hit = hit or (an[0] <= cy <= an[2] and an[1] <= cx <= an[3])
            score = iou(out.box, truth)
        avoid = c.get("avoid_label") or []
        avoid = [avoid] if isinstance(avoid, str) else avoid  # 可以是一个或一组不该选的 label
        if hit and out and any(x.lower() in (out.target_label or "").lower() for x in avoid):
            hit = False
        hits += hit
        by_group.setdefault(c.get("group", "tuned"), []).append((hit, dt))
        deep = not err and steps.LAST_THINKING.get("level") != steps.THINKING
        if deep:
            deep_rows.append((hit, dt))
        if not want_status:
            ious.append(score)
        detail = err or f"{out.status} box={out.box} label={out.target_label!r}"
        if not err:
            detail += f" thinking={steps.LAST_THINKING.get('level')}({steps.LAST_THINKING.get('reason')})"
        g = out.next_guess if out else None
        if g:
            guess_given += 1
            detail += f" guess={g.label!r}"
        if c.get("next_label"):
            guess_checked += 1
            ok = bool(g) and (c["next_label"] in g.label or g.label in c["next_label"])  # 区分大小写：OCR 要精确匹配
            guess_ok += ok
            detail += " (guess ✅)" if ok else " (guess ❌)"
        print(f"{'✅' if hit else '❌'} {(c['file'] + (':' + c['name'] if c.get('name') else '')):<32} IoU={score:.2f} {dt:5.1f}s  {detail}")
        if out:
            words = len(out.instruction.split()) if lang == "en" else len(out.instruction)
            unit = "words" if lang == "en" else "字"
            mixed = lang == "en" and bool(CJK.search(out.instruction))
            cjk_bad += mixed
            print(f"     → 「{out.instruction}」（{words} {unit}）{'  ⚠️ 英文指令里有中文，声音读不了' if mixed else ''}")
            if out.situation or out.reasoning:
                print(f"     situation: {out.situation} | reasoning: {out.reasoning} | app: {out.app}")
            if out.plan:
                print(f"     plan: {out.plan}")
    n = len(cases)
    if n:
        miou = sum(ious) / len(ious) if ious else 0
        print(f"\n[lang={a.lang or '按 case'}] 模型 {gemini.MODEL}（两步定位 {'开' if steps.REFINE else '关'}）：命中率 {hits}/{n} = {hits / n:.0%}，"
              f"平均 IoU {miou:.2f}，平均耗时 {sum(times) / n:.1f}s，最慢 {max(times):.1f}s"
              + (f"，英文指令混入中文 {cjk_bad} 条" if a.lang == "en" or cjk_bad else ""))
        print(f"  next_guess：给出 {guess_given}/{n} 次" + (f"；有标准答案的 {guess_checked} 张里猜对 {guess_ok} 张" if guess_checked else ""))
        if stage["gemini"]:
            avg = {k: sum(v) / len(v) for k, v in stage.items()}
            print(f"  阶段平均：解码+prompt {avg['prep']:.0f}ms · Gemini {avg['gemini']:.0f}ms · 修框 {avg['post']:.0f}ms"
                  f"（thinking={steps.THINKING or 'default'}）")
        if deep_rows:
            dh = sum(h for h, _ in deep_rows)
            print(f"  深度思考（thinking={steps.DEEP_THINKING}）的 {len(deep_rows)} 步：命中率 {dh}/{len(deep_rows)}，"
                  f"平均耗时 {sum(t for _, t in deep_rows) / len(deep_rows):.1f}s；其余 {n - len(deep_rows)} 步 thinking={steps.THINKING}")
        if len(by_group) > 1 or a.group:
            for g, rows in sorted(by_group.items()):
                gh = sum(h for h, _ in rows)
                print(f"  分组 {g}：命中率 {gh}/{len(rows)} = {gh / len(rows):.0%}，平均耗时 {sum(t for _, t in rows) / len(rows):.1f}s")


if __name__ == "__main__":
    main()
