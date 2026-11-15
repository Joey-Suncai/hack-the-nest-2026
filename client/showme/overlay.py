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
    if QApplication.platformName() != "cocoa":  # 离屏测试时 winId 不是 NSView，硬转会段错误
        return
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


def order_front_without_activating(widget: QWidget) -> None:
    """把窗口提到同层最前，但不激活 ShowMe、不让窗口变成 key window。

    不能用 QWidget.raise_()：Qt 在 macOS 上 raise 会顺带 activateIgnoringOtherApps（QT_MAC_SET_RAISE_PROCESS），
    用户正在用的 App 就失去焦点 —— Zoom「安排会议」这类窗口一失焦就自己关掉。"""
    if QApplication.platformName() != "cocoa" or not widget.isVisible():
        return
    try:
        import objc

        objc.objc_object(c_void_p=int(widget.winId())).window().orderFrontRegardless()
    except Exception as e:  # noqa: BLE001
        print(f"[overlay] 置前失败：{e}", flush=True)


def make_accessory_app() -> bool:
    """不显示 Dock 图标的「辅助 App」：macOS 才会把我们的窗口显示在别的桌面 / 全屏 App 上
    （普通 App 的窗口不会跟过去，CanJoinAllSpaces 不起作用）。要在建任何窗口之前调用。"""
    if QApplication.platformName() != "cocoa":
        return False
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[overlay] 设置辅助 App 失败：{e}", flush=True)
        return False


def activate_app() -> None:
    """辅助 App 默认不会抢焦点：用户点面板要打字时，主动激活。"""
    if QApplication.platformName() != "cocoa":
        return
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception as e:  # noqa: BLE001
        print(f"[overlay] 激活失败：{e}", flush=True)


_space_observer = None  # 要一直持有引用，否则会被回收


def watch_space_changes(callback) -> bool:
    """切换桌面（Space）/ 进出全屏时回调，用来重新设置窗口。"""
    global _space_observer
    if QApplication.platformName() != "cocoa":
        return False
    try:
        import objc
        from AppKit import NSWorkspace, NSWorkspaceActiveSpaceDidChangeNotification
        from Foundation import NSObject

        class _SpaceObserver(NSObject):
            def spaceChanged_(self, note):
                try:
                    callback()
                except Exception as e:  # noqa: BLE001
                    print(f"[overlay] 切换桌面回调出错：{e}", flush=True)

        _space_observer = _SpaceObserver.alloc().init()
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            _space_observer, objc.selector(_SpaceObserver.spaceChanged_, signature=b"v@:@"),
            NSWorkspaceActiveSpaceDidChangeNotification, None)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[overlay] 监听切换桌面失败：{e}", flush=True)
        return False


def _flush_core_animation() -> None:
    """repaint() 画进的内容要等 Core Animation 事务提交才送到 WindowServer；主线程接着 sleep + 截图时
    事件循环没转，可能截到旧内容。手动提交一次。"""
    if QApplication.platformName() != "cocoa":
        return
    try:
        from Quartz import CATransaction

        CATransaction.flush()
    except Exception:  # noqa: BLE001
        pass


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

        self._caption = ""
        self._caption_rect: QRect | None = None
        self._caption_font = QFont()
        self._caption_font.setPixelSize(C.CAPTION_FONT_SIZE)
        self._caption_font.setWeight(QFont.Weight.Medium)

        self._badge = ""  # 「Listening…」标签（收音时显示在屏幕下方中间：用户看着 VS Code，不一定看面板）
        self._badge_rect: QRect | None = None

        self._clock = QElapsedTimer()
        self._clock.start()
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)

        self.show()
        apply_macos_window_settings(self, click_through=True)
        self._exclude_from_capture()

    def _exclude_from_capture(self) -> None:
        """截图时让 WindowServer 直接不合成这个窗口（见 capture.EXCLUDE_WINDOW_IDS）。"""
        if QApplication.platformName() != "cocoa":
            return
        try:
            import objc

            from showme import capture

            capture.exclude_window(objc.objc_object(c_void_p=int(self.winId())).window().windowNumber())
        except Exception as e:  # noqa: BLE001
            print(f"[overlay] 截图排除高亮层失败（只靠清空重绘）：{e}", flush=True)

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

    def show_caption(self, text: str) -> None:
        """屏幕底部居中的字幕（config.CAPTIONS = False 时不显示）。"""
        if not C.CAPTIONS:
            return
        old = self._caption_rect
        self._caption = text
        self._caption_rect = self._layout_caption()
        self.update(self._caption_rect.united(old) if old else self._caption_rect)

    def show_badge(self, text: str) -> None:
        old = self._badge_rect
        self._badge = text
        fm = QFontMetrics(self._caption_font)
        w, h = fm.horizontalAdvance(text) + 64, C.CAPTION_FONT_SIZE + 26
        screen = self.rect()
        self._badge_rect = QRect(screen.center().x() - w // 2, screen.bottom() - C.CAPTION_BOTTOM - h, w, h)
        self.update(self._badge_rect.united(old) if old else self._badge_rect)

    def clear_badge(self) -> None:
        old = self._badge_rect
        self._badge, self._badge_rect = "", None
        if old:
            self.update(old)

    def clear_caption(self) -> None:
        old = self._caption_rect
        self._caption, self._caption_rect = "", None
        if old:
            self.update(old)

    def painted_rects(self) -> list[tuple[int, int, int, int]]:
        """框（含光晕）、气泡和字幕占用的区域，给画面变化检测做遮罩。"""
        out = []
        if self._rect is not None:
            # 只遮发光边框那一圈（框外扩 g、框内缩 inset），框里面照常参与比较：
            # 点输入框后唯一的变化（光标、边框变色）就在框里面
            g = C.GLOW_SIZE + C.BORDER_WIDTH
            inset = (C.GLOW_SIZE + C.BORDER_WIDTH) // 2 + 2  # 描边有一半画在框内
            outer = self._rect.adjusted(-g, -g, g, g)
            inner = self._rect.adjusted(inset, inset, -inset, -inset)
            if inner.width() <= 0 or inner.height() <= 0:
                out.append(outer)
            else:
                out += [QRect(outer.left(), outer.top(), outer.width(), inner.top() - outer.top()),       # 上
                        QRect(outer.left(), inner.bottom() + 1, outer.width(), outer.bottom() - inner.bottom()),  # 下
                        QRect(outer.left(), inner.top(), inner.left() - outer.left(), inner.height()),    # 左
                        QRect(inner.right() + 1, inner.top(), outer.right() - inner.right(), inner.height())]  # 右
        if self._bubble is not None:
            out.append(self._bubble.adjusted(-4, -4, 4, 4))
        if self._caption_rect is not None:
            out.append(self._caption_rect.adjusted(-8, -8, 8, 8))
        if self._badge_rect is not None:
            out.append(self._badge_rect.adjusted(-8, -8, 8, 8))
        return [(r.x(), r.y(), r.width(), r.height()) for r in out]

    @contextmanager
    def hidden(self):
        """截图前先把高亮层清空，截完再画回来。

        不用 hide()：macOS 隐藏窗口有淡出动画，截图里会留下上一步光圈 / 气泡的残影（联调时发现的）。
        清空内容 + 立即重绘没有动画。
        """
        saved = (self._rect, self._text, self._bubble, self._caption, self._caption_rect, self._badge, self._badge_rect)
        self._rect = self._bubble = self._caption_rect = self._badge_rect = None
        self._text = self._caption = self._badge = ""
        self._anim.stop()
        self.repaint()
        _flush_core_animation()  # 后备（排除窗口失败时）：立刻提交重绘，别等事件循环
        QApplication.processEvents()
        time.sleep(C.HIDE_WAIT_S)
        try:
            yield
        finally:
            # 截图期间如果有人调了 show_target / clear，就以新的为准
            if self._rect is None and self._bubble is None and not self._text:
                self._rect, self._text, self._bubble = saved[:3]
            if self._caption_rect is None and not self._caption:
                self._caption, self._caption_rect = saved[3], saved[4]
            if self._badge_rect is None and not self._badge:
                self._badge, self._badge_rect = saved[5], saved[6]
            if self._rect is not None and C.BREATHE_MS > 0:
                self._anim.start(33)
            self.update()

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

    def _layout_caption(self) -> QRect:
        fm = QFontMetrics(self._caption_font)
        screen = self.rect()
        max_w = int(screen.width() * C.CAPTION_MAX_W)
        px, py = 24, 14
        tr = fm.boundingRect(QRect(0, 0, max_w - 2 * px, 1000),
                             Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignHCenter, self._caption)
        w, h = tr.width() + 2 * px, tr.height() + 2 * py
        return QRect(screen.center().x() - w // 2, screen.bottom() - C.CAPTION_BOTTOM - h, w, h)

    def _paint_caption(self, p: QPainter):
        r = self._caption_rect
        p.setPen(Qt.PenStyle.NoPen)
        bg = QColor(0, 0, 0)
        bg.setAlphaF(0.72)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(r), 14, 14)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(self._caption_font)
        p.drawText(r.adjusted(24, 14, -24, -14),
                   Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   self._caption)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._caption_rect is not None:
            self._paint_caption(p)
        if self._badge_rect is not None:
            r = self._badge_rect
            p.setPen(Qt.PenStyle.NoPen)
            bg = QColor(0, 0, 0)
            bg.setAlphaF(0.8)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(r), r.height() / 2, r.height() / 2)
            p.setBrush(QColor("#FF3B30"))
            d = 12
            p.drawEllipse(QRectF(r.x() + 20, r.center().y() - d / 2, d, d))
            p.setPen(QColor("#FFFFFF"))
            p.setFont(self._caption_font)
            p.drawText(r.adjusted(40, 0, -16, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._badge)
            p.setBrush(Qt.BrushStyle.NoBrush)
        if self._rect is None:
            p.end()
            return
        p.setBrush(Qt.BrushStyle.NoBrush)  # 框里面不能填色（之前沿用了字幕的黑底）
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
