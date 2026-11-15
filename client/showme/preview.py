"""调光圈样式用：不连后端，直接在屏幕上画一个框。

  cd client && uv run python -m showme.preview                 # 默认在屏幕中间
  cd client && uv run python -m showme.preview 600 300 160 44  # x y w h（逻辑坐标，不含 PAD）
  加 --seconds 20 改显示时长

改 showme/config.py 里「高亮样式」那一节，再跑一次就能看到效果。
"""

import argparse
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from showme.config import HIGHLIGHT_PAD
from showme.coords import expand
from showme.overlay import Overlay


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rect", nargs="*", type=int, help="x y w h")
    ap.add_argument("--seconds", type=int, default=15)
    ap.add_argument("--text", default="点击上方工具栏的「图表」按钮")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    overlay = Overlay()
    screen = app.primaryScreen().geometry()
    rect = tuple(args.rect) if len(args.rect) == 4 else (screen.width() // 2 - 80, screen.height() // 2 - 22, 160, 44)
    overlay.show_target(expand(rect, HIGHLIGHT_PAD), args.text)
    QTimer.singleShot(args.seconds * 1000, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
