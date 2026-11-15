"""把一张真实截图加进评测集（backend/eval/cases/）。

1. Cmd+Shift+3 截全屏（存到桌面）
2. Cmd+Shift+4 把十字准星移到正确按钮的左上角、右下角，记下旁边显示的两组坐标（逻辑坐标），按 Esc 取消
3. 运行：
   cd backend && uv run python eval/add_case.py ~/Desktop/截屏xxx.png --name numbers_2 \\
       --app Numbers --goal "把 A1:B6 的数据做成柱状图" --history "选中 A1:B6" --pt 640 52 690 80
   （--history 可以写多次，按顺序；第一步就不写）
脚本会把截图缩到屏幕逻辑尺寸存进 cases/，写 labels.json，并打开一张画了框的预览图供核对。
"""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

CASES = Path(__file__).parent / "cases"


def logical_screen_size() -> tuple[int, int]:
    out = subprocess.run(["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'],
                         capture_output=True, text=True, check=True).stdout
    _, _, w, h = (int(v) for v in out.split(","))
    return w, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--name", required=True)
    ap.add_argument("--app", required=True)
    ap.add_argument("--goal", required=True)
    ap.add_argument("--history", action="append", default=[])
    ap.add_argument("--lang", default="zh")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pt", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    g.add_argument("--done", action="store_true", help="这张截图里任务已经完成，期望模型返回 done")
    g.add_argument("--unsure", action="store_true", help="要用户先做点什么（如 Zoom 工具栏隐藏），期望模型返回 unsure")
    ap.add_argument("--no-open", action="store_true", help="不打开预览图")
    a = ap.parse_args()

    size = logical_screen_size()
    with Image.open(Path(a.image).expanduser()) as img:
        img = img.convert("RGB").resize(size)
    file = f"{a.name}.jpg"
    img.save(CASES / file, "JPEG", quality=88)

    box = None
    if a.pt:
        x1, y1, x2, y2 = a.pt
        box = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
    labels_path = CASES / "labels.json"
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else []
    labels = [c for c in labels if c["file"] != file]
    case = {"file": file, "app": a.app, "goal": a.goal, "lang": a.lang, "history": a.history}
    case.update({"target_px": box} if box else {"expect_status": "unsure" if a.unsure else "done"})
    labels.append(case)
    labels_path.write_text(json.dumps(labels, ensure_ascii=False, indent=2) + "\n")

    preview = img.copy()
    if box:
        ImageDraw.Draw(preview).rectangle(box, outline=(255, 0, 0), width=4)
    tmp = Path(tempfile.gettempdir()) / f"showme_label_{a.name}.png"
    preview.save(tmp)
    if not a.no_open:
        subprocess.run(["open", str(tmp)])
    print(f"已加入 {file}（{size[0]}x{size[1]}），框 {box}；共 {len(labels)} 个 case。预览：{tmp}")


if __name__ == "__main__":
    main()
