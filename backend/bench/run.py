"""按任务回放的端到端评测：每个任务是一串按顺序的截图（每张 = 这一步点击之前的画面），
逐步调用真实 steps.next_step，像客户端一样把前面的指令当 history、上一步的 expect 带下去。

统计：每步命中率、任务成功率（所有步都命中）、耗时、原地打转（loop）、next_guess 是否猜中下一步。
运行：cd backend && uv run python bench/run.py [--task ID] [--runs N] [--json out.json]
没截图 / 没标注的步骤会跳过并标成「还没拍」，所以没拍任何图也能跑（只跑 vscode_clone_demo）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import gemini, steps  # noqa: E402
from eval.eval_boxes import CJK, iou, px_to_norm, to_jpeg_b64  # noqa: E402

BENCH = Path(__file__).parent
TASKS = BENCH / "tasks.json"


def load_tasks(path: Path = TASKS) -> list[dict]:
    return json.loads(Path(path).read_text())


def step_path(step: dict, base: Path = BENCH) -> Path | None:
    return (base / step["file"]).resolve() if step.get("file") else None


def skip_reason(step: dict, base: Path = BENCH) -> str | None:
    """这一步能不能跑：没截图 / 没标注就返回原因。"""
    p = step_path(step, base)
    if p is None or not p.exists():
        return "not shot yet"
    if not step.get("expect_status") and not step.get("target_px"):
        return "not labeled yet"
    return None


def labels_match(guess: str | None, label: str | None) -> bool:
    """next_guess.label 和下一步的 target_label 互为子串（区分大小写：OCR 要精确匹配）。"""
    return bool(guess) and bool(label) and (guess in label or label in guess)


def same_label(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()


def score(step: dict, out, size) -> tuple[bool, float]:
    """命中：done / unsure 步看 status；要框的步看框中心是否落在标准框里（accept_unsure 时 unsure 也算）。"""
    if out is None:
        return False, 0.0
    want = step.get("expect_status")
    if want:
        return out.status == want, 0.0
    if step.get("accept_unsure") and out.status == "unsure":
        return True, 0.0
    if not out.box:
        return False, 0.0
    truth = px_to_norm(step["target_px"], size)
    cy, cx = (out.box[0] + out.box[2]) / 2, (out.box[1] + out.box[3]) / 2
    hit = truth[0] <= cy <= truth[2] and truth[1] <= cx <= truth[3]
    return hit, iou(out.box, truth)


def run_task(task: dict, base: Path = BENCH, verbose: bool = False) -> dict:
    lang = task.get("lang", "en")
    history: list[steps.HistoryItem] = []
    expect = None
    recs: list[dict] = []
    prev_ran: dict | None = None
    for i, step in enumerate(task["steps"], 1):
        rec: dict = {"step": i, "file": step.get("file"), "target_hint": step.get("target_hint"),
                     "want": step.get("expect_status") or "box"}
        reason = skip_reason(step, base)
        if reason:
            rec["skipped"] = reason
            recs.append(rec)
            if verbose:
                print(f"  ⏭  {i:>2}. {reason}: {step.get('target_hint')}")
            continue
        b64, size = to_jpeg_b64(step_path(step, base))
        body = steps.NextStepIn(
            app=step.get("app", task["app"]), goal=task["goal"], lang=lang, screenshot=b64,
            history=list(history[-10:]), expect=expect,
            frontmost_app=step.get("frontmost_app", task.get("frontmost_app")),
        )
        rec["history_len"] = len(body.history)
        rec["expect_in"] = expect
        steps.LAST_TIMING.clear()
        t0 = time.perf_counter()
        try:
            out, err = steps.next_step(body), None
        except gemini.GeminiError as e:
            out, err = None, e.message
        except Exception as e:  # 评测不能因为一步出错整个停掉
            out, err = None, f"{type(e).__name__}: {e}"
        rec["latency_s"] = time.perf_counter() - t0
        rec["gemini_ms"] = steps.LAST_TIMING.get("gemini")
        hit, s_iou = score(step, out, size)
        rec.update(hit=hit, iou=s_iou, error=err)
        if out is not None:
            g = out.next_guess
            rec.update(status=out.status, box=out.box, instruction=out.instruction,
                       words=len(out.instruction.split()) if lang == "en" else len(out.instruction),
                       cjk_in_en=lang == "en" and bool(CJK.search(out.instruction)),
                       target_label=out.target_label, previous_ok=out.previous_ok,
                       next_guess=g.model_dump() if g else None, expect_out=out.expect)
            rec["loop"] = bool(prev_ran) and same_label(prev_ran.get("target_label"), out.target_label)
            history.append(steps.HistoryItem(instruction=out.instruction, ok=hit))
            expect = out.expect
        else:
            rec["loop"] = False
            expect = None
        # 上一步的 next_guess 有没有猜中这一步的 target_label（只和紧挨着的下一步比）
        if prev_ran is not None and prev_ran.get("next_guess") and prev_ran["step"] == i - 1:
            prev_ran["guess_hit"] = labels_match(prev_ran["next_guess"]["label"], rec.get("target_label"))
        recs.append(rec)
        prev_ran = rec
        if verbose:
            print_step(rec)
    return summarize(task, recs)


def print_step(r: dict) -> None:
    mark = "✅" if r["hit"] else "❌"
    if r.get("error"):
        print(f"  {mark} {r['step']:>2}. ERROR {r['error']}  {r['latency_s']:.1f}s")
        return
    g = r.get("next_guess")
    extra = f" guess={g['label']!r}" if g else ""
    print(f"  {mark} {r['step']:>2}. want={r['want']:<6} got={r['status']:<6} IoU={r['iou']:.2f} "
          f"{r['latency_s']:4.1f}s label={r.get('target_label')!r}{extra}{'  🔁 LOOP' if r.get('loop') else ''}")
    print(f"        → 「{r['instruction']}」（{r['words']} words）{'  ⚠️ 含中文' if r.get('cjk_in_en') else ''}")


def summarize(task: dict, recs: list[dict]) -> dict:
    ran = [r for r in recs if "skipped" not in r]
    hits = sum(r["hit"] for r in ran)
    skipped = len(recs) - len(ran)
    if not ran:
        success = None
    elif hits < len(ran):
        success = False
    else:
        success = None if skipped else True  # 有没拍的步，全对也不能算整个任务成功
    lat = [r["latency_s"] for r in ran]
    guesses = [r for r in ran if r.get("next_guess")]
    checked = [r for r in guesses if "guess_hit" in r]
    return {
        "id": task["id"], "steps": recs, "n_steps": len(recs), "n_ran": len(ran), "n_skipped": skipped,
        "hits": hits, "success": success, "total_latency_s": sum(lat),
        "mean_latency_s": sum(lat) / len(lat) if lat else 0.0, "loops": sum(bool(r.get("loop")) for r in ran),
        "guess_given": len(guesses), "guess_checked": len(checked),
        "guess_hit": sum(r["guess_hit"] for r in checked),
    }


def fmt_success(s) -> str:
    return {True: "✅ 成功", False: "❌ 失败", None: "— 不完整"}[s]


def print_task(t: dict) -> None:
    rate = f"{t['hits']}/{t['n_ran']}" + (f" = {t['hits'] / t['n_ran']:.0%}" if t["n_ran"] else "")
    skip = f"，{t['n_skipped']} 步还没拍/没标" if t["n_skipped"] else ""
    print(f"  → {t['id']}: 任务 {fmt_success(t['success'])}；步命中 {rate}{skip}；耗时合计 {t['total_latency_s']:.1f}s，"
          f"平均每步 {t['mean_latency_s']:.1f}s；loop {t['loops']}；next_guess 给出 {t['guess_given']}，"
          f"可核对 {t['guess_checked']}，猜中 {t['guess_hit']}")


def aggregate(runs: list[list[dict]]) -> dict:
    """runs[k] = 第 k 轮所有任务的 summarize 结果。"""
    by_task: dict[str, list[dict]] = {}
    for run in runs:
        for t in run:
            by_task.setdefault(t["id"], []).append(t)
    tasks = {}
    for tid, ts in by_task.items():
        rates = [t["hits"] / t["n_ran"] for t in ts if t["n_ran"]]
        tasks[tid] = {
            "runs": len(ts), "success": sum(t["success"] is True for t in ts),
            "complete": all(t["n_skipped"] == 0 for t in ts) and all(t["n_ran"] for t in ts),
            "step_hit_mean": sum(rates) / len(rates) if rates else None,
            "step_hit_min": min(rates) if rates else None, "step_hit_max": max(rates) if rates else None,
            "mean_task_latency_s": sum(t["total_latency_s"] for t in ts) / len(ts),
            "mean_step_latency_s": sum(t["mean_latency_s"] for t in ts) / len(ts),
            "loops": sum(t["loops"] for t in ts),
            "guess_given": sum(t["guess_given"] for t in ts), "guess_checked": sum(t["guess_checked"] for t in ts),
            "guess_hit": sum(t["guess_hit"] for t in ts),
        }
    # 每轮的任务成功率（只算拍完整的任务）
    per_run = []
    for run in runs:
        full = [t for t in run if t["n_ran"] and t["n_skipped"] == 0]
        if full:
            per_run.append(sum(t["success"] is True for t in full) / len(full))
    all_steps = [r for run in runs for t in run for r in t["steps"] if "skipped" not in r]
    return {
        "tasks": tasks,
        "task_success_mean": sum(per_run) / len(per_run) if per_run else None,
        "task_success_min": min(per_run) if per_run else None,
        "task_success_max": max(per_run) if per_run else None,
        "step_hits": sum(r["hit"] for r in all_steps), "steps_ran": len(all_steps),
        "mean_step_latency_s": sum(r["latency_s"] for r in all_steps) / len(all_steps) if all_steps else 0.0,
    }


def pct(x) -> str:
    return "—" if x is None else f"{x:.0%}"


def print_summary(agg: dict, n_runs: int) -> None:
    print(f"\n===== 汇总（{n_runs} 轮，模型 {gemini.MODEL}，thinking={steps.THINKING or 'default'}）=====")
    for tid, t in agg["tasks"].items():
        rng = f"（min {pct(t['step_hit_min'])} / max {pct(t['step_hit_max'])}）" if n_runs > 1 else ""
        note = "" if t["complete"] else "  [还有步骤没拍/没标，任务成功不计]"
        print(f"  {tid:<22} 任务成功 {t['success']}/{t['runs']}  步命中 {pct(t['step_hit_mean'])}{rng}  "
              f"每任务 {t['mean_task_latency_s']:.1f}s / 每步 {t['mean_step_latency_s']:.1f}s  loop {t['loops']}  "
              f"guess {t['guess_hit']}/{t['guess_checked']}（给出 {t['guess_given']}）{note}")
    if agg["steps_ran"]:
        print(f"  全部：步命中 {agg['step_hits']}/{agg['steps_ran']} = {agg['step_hits'] / agg['steps_ran']:.0%}，"
              f"平均每步 {agg['mean_step_latency_s']:.1f}s")
    if agg["task_success_mean"] is not None:
        rng = f"（min {pct(agg['task_success_min'])} / max {pct(agg['task_success_max'])}）" if n_runs > 1 else ""
        print(f"  任务成功率（只算拍完整的任务）：平均 {pct(agg['task_success_mean'])}{rng}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", help="只跑这个任务 id")
    ap.add_argument("--runs", type=int, default=1, help="重复跑几轮，汇总平均 / 最差 / 最好")
    ap.add_argument("--json", dest="json_out", help="把原始结果存成 JSON")
    a = ap.parse_args()
    if gemini.MOCK:
        sys.exit("GEMINI_MOCK=1 时评测没有意义，用 GEMINI_MOCK=0 运行")
    tasks = load_tasks()
    if a.task:
        tasks = [t for t in tasks if t["id"] == a.task]
        if not tasks:
            sys.exit(f"没有任务 {a.task}")
    runs = []
    for k in range(1, a.runs + 1):
        print(f"\n===== 第 {k}/{a.runs} 轮 =====")
        run = []
        for task in tasks:
            print(f"[{task['id']}] {task['goal']}")
            t = run_task(task, verbose=True)
            print_task(t)
            run.append(t)
        runs.append(run)
    agg = aggregate(runs)
    print_summary(agg, a.runs)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps({"model": gemini.MODEL, "runs": runs, "summary": agg},
                                               ensure_ascii=False, indent=2))
        print(f"原始结果已存到 {a.json_out}")


if __name__ == "__main__":
    main()
