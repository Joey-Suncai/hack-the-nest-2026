"""全屏透明、置顶、鼠标穿透的高亮层：发光框 + 提示气泡。样式见 config.py「高亮样式」。"""

import math
import time
from contextlib import contextmanager

from PyQt6.QtCore import QElapsedTimer, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from showme import config as C

BUBBLE_GAP = 10  # 气泡和框之间的距离
BUBBLE_PAD = (14, 9)  # 气泡内边距 (水平, 垂直)
BUBBLE_MAX_W = 420


def apply_macos_window_settings(widget: QWidget, level: int | None = None, click_through: bool = False):
    """置顶到状态栏层级、所有桌面可见、失去焦点不隐藏；可选鼠标穿透。"""
    try:
        import objc
        from AppKit import (
            NSStatusWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
        )

        win = objc.objc_object(c_void_p=int(widget.winId())).window()
        win.setLevel_(NSStatusWindowLevel if level is None else level)
        win.setHidesOnDeactivate_(False)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        if click_through:
            win.setIgnoresMouseEvents_(True)
            # 透明窗口的阴影按旧内容算、不会刷新 → 换框后旧框留下灰色残影
            win.setHasShadow_(False)
    except Exception as e:  # noqa: BLE001  不是 macOS / PyObjC 出问题时，至少别崩
        print(f"[overlay] macOS 窗口设置失败：{e}")


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

        self._rect: QRect | None = None
        self._text = ""
        self._bubble: QRect | None = None
        self._font = QFont()
        self._font.setPixelSize(C.BUBBLE_FONT_SIZE)
        self._font.setWeight(QFont.Weight.DemiBold)

        self._clock = QElapsedTimer()
        self._clock.start()
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)

        self.show()
        apply_macos_window_settings(self, click_through=True)

    # ---- 对外接口 ----
    def show_target(self, rect: tuple[int, int, int, int], text: str) -> None:
        """rect：(x, y, w, h) 逻辑坐标，已经加过 HIGHLIGHT_PAD。"""
        self._rect = QRect(*rect)
        self._text = text
        self._bubble = self._layout_bubble()
        if C.BREATHE_MS > 0:
            self._anim.start(33)
        self.update()

    def clear(self) -> None:
        self._rect, self._text, self._bubble = None, "", None
        self._anim.stop()
        self.update()

    def painted_rects(self) -> list[tuple[int, int, int, int]]:
        """框（含光晕）和气泡占用的区域，给画面变化检测做遮罩。"""
        if self._rect is None:
            return []
        g = C.GLOW_SIZE + C.BORDER_WIDTH
        out = [self._rect.adjusted(-g, -g, g, g)]
        if self._bubble is not None:
            out.append(self._bubble.adjusted(-4, -4, 4, 4))
        return [(r.x(), r.y(), r.width(), r.height()) for r in out]

    @contextmanager
    def hidden(self):
        """截图前先隐藏高亮层，截完再显示。"""
        self.hide()
        QApplication.processEvents()
        time.sleep(C.HIDE_WAIT_S)
        try:
            yield
        finally:
            self.show()
            apply_macos_window_settings(self, click_through=True)

    # ---- 绘制 ----
    def _tick(self):
        if self._rect is not None:
            g = C.GLOW_SIZE + C.BORDER_WIDTH + 2
            self.update(self._rect.adjusted(-g, -g, g, g))

    def _breath(self) -> float:
        """0.55–1.0 之间来回。"""
        if C.BREATHE_MS <= 0:
            return 1.0
        t = (self._clock.elapsed() % C.BREATHE_MS) / C.BREATHE_MS
        return 0.55 + 0.45 * (0.5 - 0.5 * math.cos(2 * math.pi * t))

    def _layout_bubble(self) -> QRect | None:
        if not self._text or self._rect is None:
            return None
        fm = QFontMetrics(self._font)
        px, py = BUBBLE_PAD
        text_rect = fm.boundingRect(QRect(0, 0, BUBBLE_MAX_W - 2 * px, 1000), Qt.TextFlag.TextWordWrap, self._text)
        w, h = text_rect.width() + 2 * px, text_rect.height() + 2 * py
        screen = self.rect()
        x = self._rect.center().x() - w // 2
        x = max(screen.left() + 8, min(x, screen.right() - w - 8))
        y = self._rect.bottom() + C.GLOW_SIZE // 2 + BUBBLE_GAP
        if y + h > screen.bottom() - 8:  # 下面放不下就放上面
            y = self._rect.top() - C.GLOW_SIZE // 2 - BUBBLE_GAP - h
        return QRect(x, max(8, y), w, h)

    def paintEvent(self, _):
        if self._rect is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(C.HIGHLIGHT_COLOR)
        k = self._breath()
        r = QRectF(self._rect)
        path = QPainterPath()
        path.addRoundedRect(r, C.CORNER_RADIUS, C.CORNER_RADIUS)

        # 光晕：一圈圈由粗到细、由淡到浓地描边
        steps = max(1, C.GLOW_SIZE // 2)
        for i in range(steps, 0, -1):
            c = QColor(color)
            c.setAlphaF(min(1.0, 0.10 * k * (1 - i / (steps + 1)) + 0.02))
            p.setPen(QPen(c, C.BORDER_WIDTH + i * 2 * (C.GLOW_SIZE / steps / 2)))
            p.drawPath(path)

        # 实线边框
        c = QColor(color)
        c.setAlphaF(0.75 + 0.25 * k)
        p.setPen(QPen(c, C.BORDER_WIDTH))
        p.drawPath(path)

        # 提示气泡
        if self._bubble is not None:
            p.setPen(Qt.PenStyle.NoPen)
            bg = QColor(C.BUBBLE_BG)
            bg.setAlphaF(0.92)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(self._bubble), 10, 10)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(self._bubble.x(), self._bubble.y() + 8, 4, self._bubble.height() - 16), 2, 2)
            p.setPen(QColor(C.BUBBLE_FG))
            p.setFont(self._font)
            px, py = BUBBLE_PAD
            p.drawText(self._bubble.adjusted(px, py, -px, -py), Qt.TextFlag.TextWordWrap, self._text)
        p.end()
