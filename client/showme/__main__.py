"""入口：cd client && uv run python -m showme（或在仓库根目录 make client）。"""

import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from showme import api
from showme.config import API_BASE
from showme.controller import Controller
from showme.overlay import Overlay
from showme.panel import Panel


def check_health(panel: Panel, tries_left: int = 5):
    """后端可能和客户端同时启动（make mock），没连上就过 2 秒再试几次。"""
    try:
        h = api.health()
    except api.ApiError as e:
        if tries_left > 0:
            panel.set_status(f"正在连接后端 {API_BASE} …")
            QTimer.singleShot(2000, lambda: check_health(panel, tries_left - 1))
        else:
            panel.set_error(e.message)
        return
    panel.set_mock(bool(h.get("mock")))
    panel.set_status(f"已连接后端（{h.get('model', '?')}）。写下目标，点「开始」")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ShowMe")
    overlay = Overlay()
    panel = Panel()
    controller = Controller(panel, overlay)  # noqa: F841  要保持引用
    QTimer.singleShot(300, lambda: check_health(panel))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
