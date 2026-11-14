"""生成两张合成截图（假的工具栏 / 设置窗口），在没有真实截图时先跑通评测，也给客户端核对坐标用。
运行：cd backend && uv run python eval/make_synthetic.py
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CASES = Path(__file__).parent / "cases"


def font(size):
    for p in ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def toolbar():
    W, H = 1280, 800
    img = Image.new("RGB", (W, H), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 28], fill=(230, 230, 230))
    d.text((60, 6), "Numbers   File   Edit   Insert   Table   Organize   Format   View", fill="black", font=font(14))
    d.rectangle([0, 28, W, 100], fill=(236, 236, 236))
    boxes = {}
    for i, name in enumerate(["View", "Zoom", "Add Category", "Pivot Table", "Insert", "Table", "Chart", "Text", "Shape", "Media", "Comment"]):
        x = 40 + i * 108
        d.rounded_rectangle([x, 40, x + 90, 88], 6, fill=(250, 250, 250), outline=(200, 200, 200))
        d.text((x + 10, 56), name, fill="black", font=font(13))
        boxes[name] = [x, 40, x + 90, 88]
    for r in range(12):
        for c in range(6):
            d.rectangle([120 + c * 120, 160 + r * 30, 240 + c * 120, 190 + r * 30], outline=(210, 210, 210), fill="white")
    for r, (k, v) in enumerate([("Month", "Sales"), ("Jan", "12"), ("Feb", "18"), ("Mar", "9"), ("Apr", "22"), ("May", "15")]):
        d.text((130, 166 + r * 30), k, fill="black", font=font(13))
        d.text((250, 166 + r * 30), v, fill="black", font=font(13))
    d.rectangle([120, 160, 360, 340], outline=(30, 120, 255), width=3)
    img.save(CASES / "synth_numbers_chart.png")
    return {"file": "synth_numbers_chart.png", "app": "Numbers", "goal": "把 A1:B6 的数据做成柱状图",
            "history": ["选中 A1:B6"], "target_px": boxes["Chart"], "note": "合成图：工具栏里的 Chart"}


def settings():
    W, H = 1280, 800
    img = Image.new("RGB", (W, H), (200, 210, 225))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([240, 100, 1040, 700], 12, fill=(250, 250, 250), outline=(180, 180, 180))
    d.rectangle([240, 100, 460, 700], fill=(238, 238, 240))
    for i, c in enumerate([(255, 95, 87), (255, 189, 46), (40, 200, 64)]):
        d.ellipse([256 + i * 20, 114, 268 + i * 20, 126], fill=c)
    boxes = {}
    items = ["Wi-Fi", "Bluetooth", "Network", "Notifications", "Sound", "Focus", "General", "Appearance",
             "Accessibility", "Control Center", "Desktop & Dock", "Displays", "Wallpaper"]
    for i, name in enumerate(items):
        y = 150 + i * 32
        d.text((280, y + 6), name, fill="black", font=font(14))
        boxes[name] = [270, y, 450, y + 28]
    d.text((490, 130), "General", fill="black", font=font(20))
    img.save(CASES / "synth_settings_dark.png")
    return {"file": "synth_settings_dark.png", "app": "System Settings", "goal": "打开深色模式",
            "history": [], "target_px": boxes["Appearance"], "note": "合成图：侧边栏 Appearance"}


if __name__ == "__main__":
    CASES.mkdir(exist_ok=True)
    labels_path = CASES / "labels.json"
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else []
    labels = [c for c in labels if not c["file"].startswith("synth_")] + [toolbar(), settings()]
    labels_path.write_text(json.dumps(labels, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {len(labels)} cases to {labels_path}")
