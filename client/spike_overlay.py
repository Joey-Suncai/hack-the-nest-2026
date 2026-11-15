"""C0 技术小实验（用完就丢）。

运行：cd client && uv run python spike_overlay.py [--seconds 30]

自动检查：
  1. 高亮层显示时，截图里能看到红框（说明层在最上面、录屏能录到）
  2. 先隐藏再截，截图里没有红框
  3. 截图像素尺寸 vs Qt 逻辑尺寸（Retina 应该差 2 倍）
  4. 是否有「屏幕录制」权限
手动检查（窗口会保留 --seconds 秒）：
  - 隔着红框能点到下面的按钮
  - 打开一个全屏应用，红框还在
"""

import argparse
import subprocess
import sys
import tempfile
import time

import mss
import numpy as np
import objc
from AppKit import (
    NSStatusWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
)
from PIL import Image
from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

BOX = QRect(200, 200, 300, 120)  # 逻辑坐标
HIDE_WAIT_S = 0.05  # 计划里 overlay.hidden() 用的等待时间


class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(QApplication.primaryScreen().geometry())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setPen(QPen(QColor(255, 0, 0), 6))
        p.drawRect(BOX)

    def apply_macos_settings(self):
        view = objc.objc_object(c_void_p=int(self.winId()))
        win = view.window()
        win.setLevel_(NSStatusWindowLevel)
        win.setIgnoresMouseEvents_(True)
        # Qt.Tool 在 macOS 上是 NSPanel，默认程序失去焦点就隐藏；用户一点别的程序红框就没了
        win.setHidesOnDeactivate_(False)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        return win


def has_screen_permission() -> bool:
    try:
        from Quartz import CGPreflightScreenCaptureAccess

        return bool(CGPreflightScreenCaptureAccess())
    except Exception as e:  # noqa: BLE001
        print(f"  （查不到权限状态：{e}）")
        return False


def grab() -> tuple[Image.Image, str]:
    try:
        with getattr(mss, "MSS", mss.mss)() as s:
            shot = s.grab(s.monitors[1])
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), "mss"
    except Exception as e:  # noqa: BLE001
        print(f"  mss 失败（{e}），改用 screencapture")
        path = tempfile.mktemp(suffix=".png")
        subprocess.run(["screencapture", "-x", "-m", path], check=True)
        return Image.open(path).convert("RGB"), "screencapture"


def red_pixels(img: Image.Image, scale: float) -> int:
    """数红框附近区域里的纯红像素个数。"""
    x0, y0 = int((BOX.x() - 10) * scale), int((BOX.y() - 10) * scale)
    x1, y1 = int((BOX.right() + 10) * scale), int((BOX.bottom() + 10) * scale)
    a = np.asarray(img.crop((x0, y0, x1, y1))).astype(int)
    mask = (a[..., 0] > 200) & (a[..., 1] < 60) & (a[..., 2] < 60)
    return int(mask.sum())


def run_checks(app: QApplication, w: Overlay, seconds: int):
    screen = app.primaryScreen()
    logical = screen.geometry()
    print("\n=== C0 技术小实验结果 ===")
    print(f"屏幕录制权限: {'有' if has_screen_permission() else '没有（系统设置 → 隐私与安全性 → 屏幕录制）'}")

    shown, how = grab()
    scale = shown.width / logical.width()
    print(f"截图方式: {how}")
    print(f"截图像素尺寸: {shown.size}，Qt 逻辑尺寸: {logical.width()}x{logical.height()}，"
          f"比例 {scale:.2f}（devicePixelRatio={screen.devicePixelRatio()}）")

    n_shown = red_pixels(shown, scale)
    print(f"[1] 显示时截图里有红框: {'是' if n_shown > 100 else '否'}（红像素 {n_shown}）")

    w.hide()
    app.processEvents()
    time.sleep(HIDE_WAIT_S)
    hidden, _ = grab()
    n_hidden = red_pixels(hidden, scale)
    w.show()
    app.processEvents()
    ok = n_hidden < 50
    print(f"[2] 隐藏 {HIDE_WAIT_S}s 后截图里没有红框: {'是' if ok else '否'}（红像素 {n_hidden}）")
    if not ok:
        for wait in (0.1, 0.2, 0.4):
            w.hide(); app.processEvents(); time.sleep(wait)
            n = red_pixels(grab()[0], scale)
            w.show(); app.processEvents()
            print(f"    再试等 {wait}s: 红像素 {n}")
            if n < 50:
                print(f"    → 建议 HIDE_WAIT_S 用 {wait}")
                break

    # 模拟用户点了别的程序：把 Finder 切到前台，红框应该还在
    subprocess.run(["osascript", "-e", 'tell application "Finder" to activate'], check=False)
    time.sleep(0.8)
    app.processEvents()
    n_other = red_pixels(grab()[0], scale)
    print(f"[3] 切到别的程序（Finder）后红框还在: {'是' if n_other > 100 else '否'}（红像素 {n_other}）")

    shown.save("/tmp/showme_spike_shown.png")
    hidden.save("/tmp/showme_spike_hidden.png")
    print("截图已存到 /tmp/showme_spike_shown.png 和 /tmp/showme_spike_hidden.png")
    print(f"\n[4] 手动检查（红框保留 {seconds} 秒）：")
    print("    - 隔着红框点下面的东西，能点到吗？")
    print("    - 打开一个全屏应用（绿色按钮），红框还在吗？")
    sys.stdout.flush()
    QTimer.singleShot(seconds * 1000, app.quit)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=30)
    args = ap.parse_args()

    app = QApplication(sys.argv)
    w = Overlay()
    w.show()
    win = w.apply_macos_settings()
    print(f"NSWindow level={win.level()} ignoresMouseEvents={win.ignoresMouseEvents()}")
    QTimer.singleShot(1000, lambda: run_checks(app, w, args.seconds))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
