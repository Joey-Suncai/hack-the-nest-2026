"""标注一张任务截图：缩到屏幕逻辑尺寸存进 bench/shots/，并把 file + target_px 写进 tasks.json。

shoot.sh 截出来的原图叫 <原图目录>/<任务id>__<两位步号>.png，比如 simple_gmail__03.png。
1. 打开原图，Cmd+Shift+4 把十字准星移到要点的元素左上角、右下角，记下两组坐标（逻辑坐标），Esc 取消
2. 运行（步号从 1 开始）：
   cd backend && uv run python bench/label.py ~/Desktop/bench_raw simple_gmail 3 --pt 640 52 690 80
   完成画面用 --done，期望模型说「先切回某软件」的用 --unsure
脚本会打开一张画了红框的预览图供核对（--no-open 不打开）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from eval.add_case import logical_screen_size  # noqa: E402

BENCH = Path(__file__).parent
TASKS = BENCH / "tasks.json"
SHOTS = BENCH / "shots"


def shot_name(task_id: str, step: int) -> str:
    return f"{task_id}__{step:02d}"


def update_step(tasks: list[dict], task_id: str, step: int, file: str, box: list[int] | None,
                status: str | None) -> dict:
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        raise SystemExit(f"tasks.json 里没有任务 {task_id}")
    if not 1 <= step <= len(task["steps"]):
        raise SystemExit(f"{task_id} 只有 {len(task['steps'])} 步（步号从 1 开始）")
    s = task["steps"][step - 1]
    s["file"] = file
    s["target_px"] = box
    if status:
        s["expect_status"] = status
    else:
        s.pop("expect_status", None)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_dir", help="shoot.sh 的输出目录")
    ap.add_argument("task")
    ap.add_argument("step", type=int, help="步号，从 1 开始")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pt", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    g.add_argument("--done", action="store_true", help="完成画面，期望模型返回 done")
    g.add_argument("--unsure", action="store_true", help="期望模型返回 unsure（比如走错软件）")
    ap.add_argument("--no-open", action="store_true", help="不打开预览图")
    a = ap.parse_args()

    name = shot_name(a.task, a.step)
    raw = Path(a.raw_dir).expanduser() / f"{name}.png"
    if not raw.exists():
        raise SystemExit(f"找不到原图 {raw}")
    tasks = json.loads(TASKS.read_text())
    box = None
    if a.pt:
        x1, y1, x2, y2 = a.pt
        box = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
    status = "done" if a.done else "unsure" if a.unsure else None
    file = f"shots/{name}.jpg"
    update_step(tasks, a.task, a.step, file, box, status)  # 先检查任务和步号，再存图

    size = logical_screen_size()
    with Image.open(raw) as img:
        img = img.convert("RGB").resize(size)
    SHOTS.mkdir(exist_ok=True)
    img.save(BENCH / file, "JPEG", quality=88)
    TASKS.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")

    preview = img.copy()
    if box:
        ImageDraw.Draw(preview).rectangle(box, outline=(255, 0, 0), width=4)
    tmp = Path(tempfile.gettempdir()) / f"showme_bench_{name}.png"
    preview.save(tmp)
    if not a.no_open:
        subprocess.run(["open", str(tmp)])
    print(f"已标注 {a.task} 第 {a.step} 步 → {file}（{size[0]}x{size[1]}），"
          f"{'框 ' + str(box) if box else '期望 ' + status}。预览：{tmp}")


if __name__ == "__main__":
    main()
