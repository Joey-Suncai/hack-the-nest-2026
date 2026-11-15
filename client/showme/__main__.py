"""入口：cd client && uv run python -m showme（或在仓库根目录 make client）。"""

import os
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from showme import api
from showme.config import API_BASE
from showme.i18n import t
from showme.controller import Controller
from showme.overlay import Overlay, apply_macos_window_settings, make_accessory_app, watch_space_changes
from showme.panel import Panel


def check_health(panel: Panel, controller=None, tries_left: int = 5):
    """后端可能和客户端同时启动（make mock），没连上就过 2 秒再试几次。"""
    try:
        h = api.health()
    except api.ApiError as e:
        if tries_left > 0:
            panel.set_status(t("connecting", api=API_BASE))
            QTimer.singleShot(2000, lambda: check_health(panel, controller, tries_left - 1))
        else:
            panel.set_error(e.message)
        return
    panel.set_mock(bool(h.get("mock")))
    panel.set_status(t("connected", model=h.get("model", "?")))
    if controller and panel.live_box.isChecked():  # 默认开实时语音；连不上会自动退回打字
        controller.set_live(True)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ShowMe")
    make_accessory_app()  # 必须在建窗口之前：框和面板才能出现在别的桌面 / 全屏 App 上
    overlay = Overlay()
    panel = Panel()

    def on_space_changed():
        print("[overlay] 切换了桌面，重新设置框和面板", flush=True)
        apply_macos_window_settings(overlay, click_through=True)
        apply_macos_window_settings(panel)
        overlay.raise_()
        panel.raise_()

    watch_space_changes(on_space_changed)
    controller = Controller(panel, overlay)
    app.aboutToQuit.connect(controller.shutdown)
    QTimer.singleShot(300, lambda: check_health(panel, controller))
    code = app.exec()
    # 后台线程可能还在等后端（比如取语音），正常析构 Qt 对象会让它们发信号时段错误 → 直接结束进程
    os._exit(code)


if __name__ == "__main__":
    main()
